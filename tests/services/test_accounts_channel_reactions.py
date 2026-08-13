"""Reactions-availability threading through the channel service.

Its own file, not more cases in ``test_accounts_channels.py``: that one sits at
the repo's 700-line cap for test sources, and the three assertions here are the
ones nothing else can make. Each pins one crossing of the service boundary that
a dropped keyword argument would silently break — the operator's choice would
be ignored on create, the editor's toggle would no-op (or 400, since an
``EditChannel`` with every field ``None`` fails its validator), and a channel
whose reactions are off would render as on, leaving the toggle unable to change
anything.

Same patch seams as its sibling: ``execute`` / ``execute_read`` on the owning
``services.accounts.channels`` module.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from core.telegram_client._react import _whitelist_cache, dispatch_react_to_post
from schemas.channels import ChannelCreateRequest, ChannelUpdateRequest
from schemas.telegram_actions import ActionResult, CreateChannel, EditChannel, ReactToPost
from schemas.telegram_actions_channels import TelegramOwnChannelDetail
from services.accounts import (
    create_account_channel,
    get_account_channel,
    update_account_channel,
)
from services.accounts._result import AccountActionError

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from pydantic import BaseModel


@pytest.fixture(autouse=True)
def _isolate_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.telegram, "session_dir", tmp_path / "sessions")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()
    _whitelist_cache.clear()
    yield
    _whitelist_cache.clear()
    reset_logging_for_tests()


def _patch_execute(monkeypatch: pytest.MonkeyPatch, action_type: str) -> list[object]:
    captured: list[object] = []

    async def fake_execute(account_id: str, action: object) -> ActionResult:
        captured.append(action)
        return ActionResult(status="ok", action_type=action_type, account_id=account_id)

    monkeypatch.setattr("services.accounts.channels.execute", fake_execute)
    return captured


def _patch_read(monkeypatch: pytest.MonkeyPatch, result: BaseModel) -> None:
    async def fake_execute_read(_account_id: str, _action: object) -> BaseModel:
        return result

    monkeypatch.setattr("services.accounts.channels.execute_read", fake_execute_read)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.asyncio
async def test_create_account_channel_threads_reactions_choice(
    monkeypatch: pytest.MonkeyPatch,
    *,
    enabled: bool,
) -> None:
    captured = _patch_execute(monkeypatch, "channel_create")
    _whitelist_cache["reused-handle"] = (time.monotonic(), {"🔥"})

    await create_account_channel(
        "acc-1",
        ChannelCreateRequest(title="Silent", reactions_enabled=enabled),
    )

    action = captured[0]
    assert isinstance(action, CreateChannel)
    assert action.reactions_enabled is enabled
    assert _whitelist_cache == {}


@pytest.mark.asyncio
async def test_update_account_channel_threads_a_reactions_only_edit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reactions alone must reach the action — and satisfy its validator."""
    captured = _patch_execute(monkeypatch, "channel_edit")
    _whitelist_cache["cached-channel"] = (time.monotonic(), {"🔥"})

    await update_account_channel("acc-1", 42, ChannelUpdateRequest(reactions_enabled=False))

    action = captured[0]
    assert isinstance(action, EditChannel)
    assert action.reactions_enabled is False
    assert action.title is None
    assert action.about is None
    assert _whitelist_cache == {}


@pytest.mark.asyncio
async def test_reactions_toggle_fences_an_in_flight_cached_reaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful disable wins over a react paused after its cache hit."""
    _patch_execute(monkeypatch, "channel_edit")
    _whitelist_cache["race-channel"] = (time.monotonic(), {"🔥"})
    peer_lookup_started = asyncio.Event()
    release_peer_lookup = asyncio.Event()
    sent: list[object] = []

    class GatedClient:
        async def get_input_entity(self, channel: str) -> str:
            peer_lookup_started.set()
            await release_peer_lookup.wait()
            return f"peer:{channel}"

        async def __call__(self, request: object) -> None:
            sent.append(request)

    react_task = asyncio.create_task(
        dispatch_react_to_post(
            GatedClient(),  # ty: ignore[invalid-argument-type]
            ReactToPost(channel="race-channel", reactions=["🔥"], message_ids=[11]),
        ),
    )
    await peer_lookup_started.wait()

    await update_account_channel(
        "owner-account",
        42,
        ChannelUpdateRequest(reactions_enabled=False),
    )
    release_peer_lookup.set()
    outcome = await react_task

    assert outcome.message_id is None
    assert outcome.log_extra == {"reaction_skip": "reaction_settings_changed"}
    assert sent == []


@pytest.mark.asyncio
async def test_non_reaction_edit_preserves_whitelist_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A title-only edit does not evict an unrelated warming cache entry."""
    _patch_execute(monkeypatch, "channel_edit")
    cached = (time.monotonic(), {"🔥"})
    _whitelist_cache["stable-channel"] = cached

    await update_account_channel("acc-1", 42, ChannelUpdateRequest(title="Renamed"))

    assert _whitelist_cache == {"stable-channel": cached}


@pytest.mark.asyncio
async def test_unconfirmed_reaction_toggle_still_invalidates_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lost acknowledgement may hide an applied remote mutation; fail closed."""

    async def _unconfirmed(account_id: str, _action: object) -> ActionResult:
        return ActionResult(
            status="unavailable",
            action_type="channel_edit",
            account_id=account_id,
            error_type="UnconfirmedDispatch",
        )

    monkeypatch.setattr("services.accounts.channels.execute", _unconfirmed)
    _whitelist_cache["maybe-disabled"] = (time.monotonic(), {"🔥"})

    with pytest.raises(AccountActionError):
        await update_account_channel(
            "acc-1",
            42,
            ChannelUpdateRequest(reactions_enabled=False),
        )

    assert _whitelist_cache == {}


@pytest.mark.asyncio
async def test_get_account_channel_maps_reactions_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``False`` on purpose, because both models default to ``True``.

    A mapping that dropped the field would otherwise pass an assertion made
    against that shared default.
    """
    _patch_read(
        monkeypatch,
        TelegramOwnChannelDetail(
            channel_id=42,
            title="Silent",
            about="",
            reactions_enabled=False,
        ),
    )

    view = await get_account_channel("acc-1", 42)

    assert view.reactions_enabled is False
