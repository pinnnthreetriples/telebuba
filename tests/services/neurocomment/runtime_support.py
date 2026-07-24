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
)
from services.neurocomment import _runtime, _state

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
    _runtime.reset_for_tests()
    _state.reset_for_tests()
    yield
    _runtime.reset_for_tests()
    _state.reset_for_tests()


class _ListenerSpy:
    def __init__(self) -> None:
        self.subscribed: list[tuple[str, list[str]]] = []
        self.stopped: list[str] = []
        self.on_post: Callable[[NewPostEvent], Awaitable[None]] | None = None

    async def subscribe_posts(
        self,
        account_id: str,
        channels: list[str],
        on_post: Callable[[NewPostEvent], Awaitable[None]],
    ) -> None:
        self.subscribed.append((account_id, channels))
        self.on_post = on_post

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
