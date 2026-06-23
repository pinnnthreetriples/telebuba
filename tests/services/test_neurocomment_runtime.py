"""Tests for ``services.neurocomment._runtime`` — listener wiring + task ownership.

The gateway listener (``subscribe_posts`` / ``stop_post_listener``) and the
on-post pipeline (``handle_new_post``) are patched on the runtime module so the
reconcile/shutdown logic runs with no Telegram and no real pipeline. Mirrors the
warming runtime tests' approach to task tracking + shutdown.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (
    configure_database,
    create_campaign,
    link_channel_to_campaign,
)
from core.logging import reset_logging_for_tests, setup_logging
from schemas.neurocomment import CampaignCreate
from schemas.telegram_actions import ActionResult, ActionStatus, JoinChannel, NewPostEvent
from services.neurocomment import _runtime

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()
    _runtime.reset_for_tests()
    yield
    _runtime.reset_for_tests()


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


@pytest.mark.asyncio
async def test_reconcile_subscribes_with_active_watch_channels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, "@a")
    await link_channel_to_campaign(campaign.campaign_id, "@b")
    spy = _ListenerSpy()
    _patch_listener(monkeypatch, spy)
    exec_spy = _ExecuteSpy()
    _patch_execute(monkeypatch, exec_spy)

    await _runtime.reconcile_neurocomment_runtime("listener-1")

    assert len(spy.subscribed) == 1
    account_id, channels = spy.subscribed[0]
    assert account_id == "listener-1"
    assert set(channels) == {"@a", "@b"}
    assert spy.stopped == []
    # The listener account is joined to every watched channel before subscribing.
    assert {ch for _aid, ch in exec_spy.joined} == {"@a", "@b"}
    assert all(aid == "listener-1" for aid, _ch in exec_spy.joined)


@pytest.mark.asyncio
async def test_reconcile_with_no_channels_stops_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _ListenerSpy()
    _patch_listener(monkeypatch, spy)

    await _runtime.reconcile_neurocomment_runtime("listener-1")

    assert spy.subscribed == []
    assert spy.stopped == ["listener-1"]


@pytest.mark.asyncio
async def test_reconcile_join_failure_does_not_block_subscribe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, "@a")
    spy = _ListenerSpy()
    _patch_listener(monkeypatch, spy)
    _patch_execute(monkeypatch, _ExecuteSpy(ok=False))

    await _runtime.reconcile_neurocomment_runtime("listener-1")

    # A failed join must not stop the listener from subscribing.
    assert len(spy.subscribed) == 1
    assert spy.subscribed[0][1] == ["@a"]


@pytest.mark.asyncio
async def test_on_post_spawns_task_and_returns_without_blocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_handle(_event: NewPostEvent) -> None:
        started.set()
        await release.wait()

    monkeypatch.setattr(_runtime, "handle_new_post", slow_handle)

    # The callback must return immediately even though the handler blocks.
    await asyncio.wait_for(
        _runtime.on_post(NewPostEvent(channel="@a", post_id=1, text="hi")),
        timeout=0.5,
    )
    await asyncio.wait_for(started.wait(), timeout=0.5)
    assert len(_runtime._TASKS) == 1

    release.set()
    await asyncio.sleep(0)  # let the task finish + discard itself
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_shutdown_stops_listener_and_cancels_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _ListenerSpy()
    _patch_listener(monkeypatch, spy)
    cancelled = asyncio.Event()

    async def never_ending(_event: NewPostEvent) -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(_runtime, "handle_new_post", never_ending)
    await _runtime.on_post(NewPostEvent(channel="@a", post_id=1, text="hi"))
    await asyncio.sleep(0)  # let the task start

    await _runtime.shutdown_neurocomment_runtime("listener-1")

    assert spy.stopped == ["listener-1"]
    assert cancelled.is_set()
    assert not _runtime._TASKS


@pytest.mark.asyncio
async def test_shutdown_with_no_tasks_just_stops_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _ListenerSpy()
    _patch_listener(monkeypatch, spy)

    await _runtime.shutdown_neurocomment_runtime("listener-1")

    assert spy.stopped == ["listener-1"]
    assert not _runtime._TASKS
