"""Shared fixtures and stubs for neurocomment runtime tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (
    configure_database,
)
from core.logging import reset_logging_for_tests, setup_logging
from schemas.telegram_actions import (
    ActionResult,
    ActionStatus,
    JoinChannel,
    NewPostEvent,
    TelegramAction,
)
from services.neurocomment import _inbox_runtime, _runtime, _seams, _state

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator
    from pathlib import Path


@pytest.fixture
def isolate_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()
    # Collapse the jittered inter-join pause so multi-channel reconciles don't
    # actually wait 30-120s per join in tests.
    monkeypatch.setattr(_runtime, "_join_jitter_seconds", lambda: 0.0)
    # No test may reach the gateway by accident: the sweep's re-join give-up leaves the
    # chat, so a test that only ticks the sweep would otherwise open a real client (and
    # leak its Telethon session handle). Tests asserting on actions override this.
    monkeypatch.setattr(_seams, "execute", _ok_action)

    async def _no_backfill(*_args: object, **_kwargs: object) -> list[NewPostEvent]:
        return []

    monkeypatch.setattr(_inbox_runtime, "fetch_recent_posts", _no_backfill)
    _runtime.reset_for_tests()
    _state.reset_for_tests()
    yield
    _runtime.reset_for_tests()
    _state.reset_for_tests()


async def _ok_action(account_id: str, action: TelegramAction) -> ActionResult:
    """The gateway's shape without the gateway — no client, no session, no socket."""
    return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)


class _ListenerSpy:
    def __init__(self, *, unresolvable: set[str] | None = None) -> None:
        self.subscribed: list[tuple[str, list[str]]] = []
        self.stopped: list[str] = []
        self.on_post: Callable[[NewPostEvent], Awaitable[None]] | None = None
        # Channels the fake listener cannot resolve: they are recorded as requested but
        # left out of the returned watch set, mirroring a failed ``get_peer_id``.
        self.unresolvable = unresolvable or set()

    async def subscribe_posts(
        self,
        account_id: str,
        channels: list[str],
        on_post: Callable[[NewPostEvent], Awaitable[None]],
    ) -> list[str]:
        self.subscribed.append((account_id, channels))
        self.on_post = on_post
        return [channel for channel in channels if channel not in self.unresolvable]

    async def stop_post_listener(self, account_id: str) -> None:
        self.stopped.append(account_id)


def _patch_listener(monkeypatch: pytest.MonkeyPatch, spy: _ListenerSpy) -> None:
    monkeypatch.setattr(_runtime, "subscribe_posts", spy.subscribe_posts)
    monkeypatch.setattr(_runtime, "stop_post_listener", spy.stop_post_listener)


class _ExecuteSpy:
    """Records the JoinChannel calls reconcile makes through the gateway seam."""

    def __init__(self, *, ok: bool = True) -> None:
        self.ok = ok
        self.joined: list[tuple[str, str]] = []

    async def execute(self, account_id: str, action: JoinChannel) -> ActionResult:
        self.joined.append((account_id, action.channel))
        status: ActionStatus = "ok" if self.ok else "failed"
        return ActionResult(status=status, action_type=action.action_type, account_id=account_id)


def _patch_execute(monkeypatch: pytest.MonkeyPatch, spy: _ExecuteSpy) -> None:
    monkeypatch.setattr("services.neurocomment._seams.execute", spy.execute)


def _patch_warming_ids(monkeypatch: pytest.MonkeyPatch, ids: set[str]) -> None:
    async def _ids() -> set[str]:
        return set(ids)

    monkeypatch.setattr(_runtime, "list_warming_account_ids", _ids)


async def _drain_joins() -> None:
    """Await the background paced-join task so join/sleep/cache assertions see it finish.

    Since the paced join loop moved off reconcile's hot path (it now returns before the
    joins land), tests that assert ``exec_spy.joined`` / ``_JOINED_CHANNELS`` / sleep
    counts must drain the coalescing task first. The task loops until no rerun is queued,
    so awaiting it once covers any coalesced rerun. No-op when no pass is in flight.
    """
    task = _runtime._JOIN_TASK
    if task is not None:
        await task
