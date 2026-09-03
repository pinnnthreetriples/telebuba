"""Shared fixtures and stubs for channel-discovery service tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from core.config import settings
from core.db import configure_database, create_account, create_campaign, set_listener_account_id
from core.logging import reset_logging_for_tests, setup_logging
from core.telegram_client import TelegramReadError
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate
from schemas.neurocomment_discovery import DiscoverySearchOutcome, DiscoverySearchRequest
from schemas.telegram_actions import (
    GetSimilarChannels,
    LinkedDiscussionGroupResult,
    SearchChannels,
    SearchGlobalPosts,
)
from schemas.telegram_actions_discovery import (
    GlobalPostsCursor,
    TelegramChannelMatch,
    TelegramChannelMatches,
    TelegramGlobalPostMatches,
)
from services import warming
from services.neurocomment import _discovery_state, _seams, _state
from services.neurocomment.discovery import start_discovery

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from pydantic import BaseModel

    from core.telegram_client._read import ReadErrorKind
    from schemas.telegram_actions import TelegramReadAction

_Scripted = "BaseModel | Callable[[TelegramReadAction], BaseModel]"

LISTENER_ID = "acc-listener"
_NO_SUCH_CAMPAIGN = "start_discovery refused: the campaign does not exist"


@pytest.fixture
def isolate_discovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()
    # Pacing must never actually sleep in tests, and jitter must be deterministic.
    monkeypatch.setattr(_seams, "sleep", _no_sleep)
    monkeypatch.setattr(_seams.rng, "uniform", lambda low, _high: low)
    _state.reset_for_tests()
    _discovery_state.reset_for_tests()
    # ``start_discovery`` claims the account under warming's per-account lifecycle lock,
    # and that table is module-level and bound to the loop alive when each lock was
    # created. Cleared for the reason warming's own conftest clears it: a lock built in
    # an earlier test's loop raises "bound to a different event loop" the moment this
    # one waits on it, which is an ordering-dependent failure, not a real race.
    warming._ACCOUNT_LOCKS.clear()
    yield
    _discovery_state.reset_for_tests()
    _state.reset_for_tests()
    warming._ACCOUNT_LOCKS.clear()


async def _no_sleep(_seconds: float) -> None:
    return None


async def seed_listener(account_id: str = LISTENER_ID) -> str:
    """Create an account and pin it as the fleet listener (discovery's read account)."""
    await create_account(
        AccountCreate(account_id=account_id, label="listener", session_name=account_id)
    )
    await set_listener_account_id(account_id)
    return account_id


async def new_campaign() -> str:
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    return campaign.campaign_id


def search_request(**overrides: object) -> DiscoverySearchRequest:
    payload: dict[str, object] = {"keywords": ["crypto"], "account_ids": [LISTENER_ID]}
    payload.update(overrides)
    return DiscoverySearchRequest.model_validate(payload)


async def drain_discovery(campaign_id: str) -> None:
    """Await the spawned run so a test can assert on its terminal phase."""
    task = _discovery_state._TASKS.get(campaign_id)
    if task is not None:
        await task


async def start_run(campaign_id: str, request: DiscoverySearchRequest) -> DiscoverySearchOutcome:
    """``start_discovery`` narrowed to its outcome.

    Every caller here starts from a campaign it just created, so the unknown-campaign
    branch would only add ten unrelated None-checks. The route's 404 is covered by the
    API tests, and refusing an unknown campaign by ``test_discovery_run``.
    """
    outcome = await start_discovery(campaign_id, request)
    if outcome is None:
        raise AssertionError(_NO_SUCH_CAMPAIGN)
    return outcome


def matches(*rows: tuple[str, str, int | None]) -> TelegramChannelMatches:
    return TelegramChannelMatches(
        items=[
            TelegramChannelMatch(username=username, title=title, participants_count=count)
            for username, title, count in rows
        ],
    )


def posts_page(
    *rows: tuple[str, str, int | None],
    cursor: GlobalPostsCursor | None = None,
) -> TelegramGlobalPostMatches:
    """One page of the global post search. ``cursor=None`` = nothing to page on from."""
    return TelegramGlobalPostMatches(items=matches(*rows).items, next_cursor=cursor)


class ReadRecorder:
    """Stub for ``_seams.execute_read`` that scripts results per action type.

    ``search`` / ``similar`` / ``posts`` / ``linked`` are looked up by the action's
    discriminator, so one recorder serves both discovery stages. A callable value is
    invoked with the action (for per-channel verdicts, per-page results or raising);
    anything else is returned as-is.
    """

    def __init__(
        self,
        *,
        search: object = None,
        similar: object = None,
        posts: object = None,
        linked: object = None,
    ) -> None:
        self._by_type: dict[str, object] = {  # heterogeneous by design (value or factory)
            "search_channels": search if search is not None else matches(),
            "get_similar_channels": similar if similar is not None else matches(),
            "search_global_posts": posts if posts is not None else posts_page(),
            "get_linked_discussion_group": (
                linked
                if linked is not None
                else LinkedDiscussionGroupResult(linked_chat_id=-1, comments_enabled=True)
            ),
        }
        self.calls: list[TelegramReadAction] = []

    async def __call__(self, _account_id: str, action: TelegramReadAction) -> BaseModel:
        self.calls.append(action)
        scripted = self._by_type[action.action_type]
        if callable(scripted):
            factory = cast("Callable[[TelegramReadAction], BaseModel]", scripted)
            return factory(action)
        return cast("BaseModel", scripted)

    def actions_of(self, action_type: str) -> list[TelegramReadAction]:
        return [call for call in self.calls if call.action_type == action_type]

    def search_actions(self) -> list[SearchChannels]:
        """Keyword-search actions, narrowed so tests can read ``.query``."""
        return [call for call in self.calls if isinstance(call, SearchChannels)]

    def similar_actions(self) -> list[GetSimilarChannels]:
        """Recommendation actions, narrowed so tests can read ``.seed``."""
        return [call for call in self.calls if isinstance(call, GetSimilarChannels)]

    def posts_actions(self) -> list[SearchGlobalPosts]:
        """Global post-search actions, narrowed so tests can read ``.query``/``.cursor``."""
        return [call for call in self.calls if isinstance(call, SearchGlobalPosts)]


def read_error(
    reason: str,
    only: str | None = None,
    *,
    kind: ReadErrorKind = "other",
    seconds: int | None = None,
) -> Callable[[TelegramReadAction], BaseModel]:
    """Scripted gateway failure for :class:`ReadRecorder`.

    Keeps the reason string out of the test body (ruff EM101/TRY003) and, with
    ``only``, fails just the channels whose handle contains that fragment. ``kind`` is
    the gateway's own classification, which is what discovery reads — never the reason
    text, since only one member of the rate-limit family spells itself "FloodWait".
    """

    def _raise(action: TelegramReadAction) -> BaseModel:
        channel = getattr(action, "channel", "")
        if only is None or only in channel:
            raise TelegramReadError(reason, kind=kind, seconds=seconds)
        return LinkedDiscussionGroupResult(linked_chat_id=-100, comments_enabled=True)

    return _raise


def flood_error(
    seconds: int,
    only: str | None = None,
    *,
    reason: str = "FloodWait",
) -> Callable[[TelegramReadAction], BaseModel]:
    """A rate limit exactly as ``core.telegram_client._read`` classifies one."""
    return read_error(f"{reason}({seconds}s)", only, kind="flood_wait", seconds=seconds)
