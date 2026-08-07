"""Shared fixtures and stubs for neurocomment onboarding tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import pytest

from core.config import settings
from core.db import (
    configure_database,
)
from core.logging import reset_logging_for_tests, setup_logging
from schemas.gemini import GeminiResult
from schemas.spam_status import SpamStatusVerdict
from schemas.telegram_actions import (
    ActionResult,
    BanCheckResult,
    BotChallengeWaitResult,
    CheckBannedInChannel,
    LinkedDiscussionGroupResult,
    WaitForBotChallenge,
)
from services.neurocomment import _seams, _state

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from schemas.challenge import BotChallengeMessage
    from schemas.telegram_actions import ActionStatus, TelegramAction, TelegramReadAction

_BanState = Literal["can_send", "restricted", "not_member", "comments_disabled"]

# The real gateway seam, captured at import time — i.e. before ``isolate_onboarding``
# replaces it with ``_ok_action``. A test that has to prove an action reaches Telethon
# for real (rather than that the rule ASKED for one) restores this and stubs the client
# underneath it instead; without the capture the default stub silently swallows the very
# dispatch such a test exists to check.
real_execute = _seams.execute


@pytest.fixture
def isolate_onboarding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    # GeminiRequest requires a non-empty key (the solver builds one); CI has none.
    monkeypatch.setattr(settings.gemini, "api_key", "test-key")
    reset_logging_for_tests()
    setup_logging()
    # onboard_campaign probes each account's spam once; keep it off the network.
    monkeypatch.setattr(_seams, "refresh_spam_status", _clean_spam)
    # Default the ACTION seam the same way, so a rule that reaches Telegram from a path a
    # test did not stub cannot open a real client: the re-join give-up leaves the chat, and
    # a test that only parks a pair and ticks the review used to dial Telegram for real
    # (and leak the Telethon session's sqlite handle). Tests asserting on actions override
    # this with their own stub.
    monkeypatch.setattr(_seams, "execute", _ok_action)
    # The solver calls Gemini on a detected (non-image) challenge — keep it off the
    # network; an error verdict makes the solver give up (→ bot_challenge).
    monkeypatch.setattr(_seams, "generate_text", _gemini_error)
    # The solver is opt-in (#148, default off); enable it for the tests that assert
    # solver behaviour — the gating tests override this per case.
    monkeypatch.setattr(settings.neurocomment, "challenge_solver_enabled", True)
    _state.reset_for_tests()
    yield
    _state.reset_for_tests()
    reset_logging_for_tests()


async def _gemini_error(_request: object) -> GeminiResult:
    return GeminiResult(status="error", error="offline in tests")


async def _ok_action(account_id: str, action: TelegramAction) -> ActionResult:
    """The gateway's shape without the gateway — no client, no session, no socket."""
    return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)


class _ReadStub:
    """Canned reads: a linked-group result for resolve, a wait result for the solver."""

    def __init__(
        self,
        *,
        linked_chat_id: int | None,
        comments_enabled: bool,
        challenge: BotChallengeMessage | None = None,
        ban_state: _BanState = "restricted",
    ) -> None:
        self.result = LinkedDiscussionGroupResult(
            linked_chat_id=linked_chat_id,
            comments_enabled=comments_enabled,
        )
        self.challenge = challenge
        # Only the join-time ban branch probes this, and it does so to confirm the group
        # itself banned us — so "restricted" (the confirming verdict) is the useful default.
        self.ban_state = ban_state
        self.calls: list[tuple[str, TelegramReadAction]] = []

    async def execute_read(self, account_id: str, action: TelegramReadAction) -> object:
        self.calls.append((account_id, action))
        if isinstance(action, WaitForBotChallenge):
            return BotChallengeWaitResult(message=self.challenge)
        if isinstance(action, CheckBannedInChannel):
            return BanCheckResult(state=self.ban_state)
        return self.result


class _JoinStub:
    """Returns a canned join ``ActionResult`` keyed by channel, default ok."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, TelegramAction]] = []
        self.by_channel: dict[str, ActionResult] = {}

    def set(
        self,
        channel: str,
        *,
        status: ActionStatus,
        error_type: str | None = None,
        flood_wait_seconds: int | None = None,
    ) -> None:
        self.by_channel[channel] = ActionResult(
            status=status,
            action_type="join_discussion_group",
            account_id="x",
            error_type=error_type,
            flood_wait_seconds=flood_wait_seconds,
        )

    async def execute(self, account_id: str, action: TelegramAction) -> ActionResult:
        self.calls.append((account_id, action))
        channel = getattr(action, "channel", "")
        if channel in self.by_channel:
            return self.by_channel[channel]
        return ActionResult(
            status="ok",
            action_type=action.action_type,
            account_id=account_id,
        )


def _no_sleep(records: list[float]) -> object:
    async def _sleep(seconds: float) -> None:
        records.append(seconds)

    return _sleep


async def _clean_spam(account_id: str, **_kwargs: object) -> SpamStatusVerdict:
    return SpamStatusVerdict(
        account_id=account_id, status="clean", checked_at="2026-01-01T00:00:00"
    )
