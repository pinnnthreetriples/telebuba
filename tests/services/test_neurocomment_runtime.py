"""Tests for ``services.neurocomment._runtime`` — listener wiring + task ownership.

The gateway listener (``subscribe_posts`` / ``stop_post_listener``) and the
on-post pipeline (``handle_new_post``) are patched on the runtime module so the
reconcile/shutdown logic runs with no Telegram and no real pipeline. Mirrors the
warming runtime tests' approach to task tracking + shutdown.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (
    claim_comment,
    configure_database,
    create_account,
    create_campaign,
    get_listener_account_id,
    link_channel_to_campaign,
    mark_comment_posted,
    set_listener_account_id,
)
from core.logging import reset_logging_for_tests, setup_logging
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate
from schemas.telegram_actions import (
    ActionResult,
    ActionStatus,
    CheckMessagesAlive,
    CheckMessagesAliveResult,
    JoinChannel,
    NewPostEvent,
)
from services.neurocomment import _runtime, _state

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


@pytest.mark.asyncio
async def test_runtime_status_stopped_when_no_listener_persisted() -> None:
    status = await _runtime.neurocomment_runtime_status()
    assert status.running is False
    assert status.active_channels == 0
    assert status.listener_account_id is None


@pytest.mark.asyncio
async def test_runtime_status_running_counts_active_watch_channels() -> None:
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, "@a")
    await link_channel_to_campaign(campaign.campaign_id, "@b")
    await set_listener_account_id("listener-1")

    status = await _runtime.neurocomment_runtime_status()

    assert status.running is True
    assert status.active_channels == 2
    assert status.listener_account_id == "listener-1"


@pytest.mark.asyncio
async def test_runtime_status_running_with_no_channels_reports_zero() -> None:
    # A persisted listener with an empty watch set still reads as running (the
    # listener is up); the count is simply 0.
    await set_listener_account_id("listener-1")

    status = await _runtime.neurocomment_runtime_status()

    assert status.running is True
    assert status.active_channels == 0


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
    # reconcile also started the deletion sweep — tear it down (strict-mode loop hygiene).
    await _runtime.shutdown_neurocomment_runtime("listener-1")


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
    await _runtime.shutdown_neurocomment_runtime("listener-1")


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


# --------------------------------------------------------------------------- #
# Service entrypoints (#119): start/stop/reconcile-on-startup/shutdown-on-shutdown.
# --------------------------------------------------------------------------- #


class _ReconcileSpy:
    def __init__(self) -> None:
        self.reconciled: list[str] = []
        self.shut_down: list[str] = []

    async def reconcile(self, account_id: str) -> None:
        self.reconciled.append(account_id)

    async def shutdown(self, account_id: str) -> None:
        self.shut_down.append(account_id)


def _patch_engine(monkeypatch: pytest.MonkeyPatch, spy: _ReconcileSpy) -> None:
    monkeypatch.setattr(_runtime, "reconcile_neurocomment_runtime", spy.reconcile)
    monkeypatch.setattr(_runtime, "shutdown_neurocomment_runtime", spy.shutdown)


@pytest.mark.asyncio
async def test_start_persists_listener_then_reconciles(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _ReconcileSpy()
    _patch_engine(monkeypatch, spy)

    await _runtime.start_neurocomment("listener-1")

    assert await get_listener_account_id() == "listener-1"
    assert spy.reconciled == ["listener-1"]


@pytest.mark.asyncio
async def test_start_runs_onboarding_for_each_active_campaign_before_reconcile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One Start click does the full setup: onboard all active campaigns then point the listener.

    Closes the failure mode where comments did not flow until the operator
    re-pressed Onboarding after Start.
    """
    active_a = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    active_b = await create_campaign(CampaignCreate(name="B", prompt="p", status="active"))
    paused = await create_campaign(CampaignCreate(name="C", prompt="p", status="paused"))
    spy = _ReconcileSpy()
    _patch_engine(monkeypatch, spy)
    order: list[str] = []

    async def fake_onboard(campaign_id: str, **_kwargs: object) -> object:
        order.append(f"onboard:{campaign_id}")
        return None

    async def fake_reconcile(account_id: str) -> None:
        order.append(f"reconcile:{account_id}")
        spy.reconciled.append(account_id)

    monkeypatch.setattr(_runtime, "onboard_campaign", fake_onboard)
    monkeypatch.setattr(_runtime, "reconcile_neurocomment_runtime", fake_reconcile)

    await _runtime.start_neurocomment("listener-1")

    # Both active campaigns were onboarded BEFORE the listener was pointed at them,
    # and the paused campaign was left alone.
    onboarded = {step.removeprefix("onboard:") for step in order if step.startswith("onboard:")}
    assert onboarded == {active_a.campaign_id, active_b.campaign_id}
    assert paused.campaign_id not in onboarded
    assert order[-1] == "reconcile:listener-1"
    assert await get_listener_account_id() == "listener-1"


@pytest.mark.asyncio
async def test_start_continues_to_next_campaign_when_one_onboard_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One campaign's onboarding raise must not blackhole Start.

    The listener still gets persisted and reconcile still fires, so the runtime
    is up even if one campaign needs operator follow-up. Bug 1: a transient
    SQLite/onboarding error previously aborted the whole Start.
    """
    a = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    b = await create_campaign(CampaignCreate(name="B", prompt="p", status="active"))
    spy = _ReconcileSpy()
    _patch_engine(monkeypatch, spy)
    onboarded: list[str] = []

    async def fake_onboard(campaign_id: str, **_kwargs: object) -> object:
        onboarded.append(campaign_id)
        if campaign_id == a.campaign_id:
            msg = "transient sqlite error"
            raise RuntimeError(msg)
        return None

    monkeypatch.setattr(_runtime, "onboard_campaign", fake_onboard)

    await _runtime.start_neurocomment("listener-1")

    # Both campaigns were attempted (the raise didn't short-circuit the loop),
    # the listener was still persisted, and reconcile still fired.
    assert set(onboarded) == {a.campaign_id, b.campaign_id}
    assert await get_listener_account_id() == "listener-1"
    assert spy.reconciled == ["listener-1"]


@pytest.mark.asyncio
async def test_start_passes_on_progress_through_to_onboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug 13: start_neurocomment accepts and forwards on_progress to onboard_campaign."""
    await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    spy = _ReconcileSpy()
    _patch_engine(monkeypatch, spy)
    seen: list[object] = []

    async def fake_onboard(_campaign_id: str, *, on_progress: object = None) -> object:
        seen.append(on_progress)
        return None

    monkeypatch.setattr(_runtime, "onboard_campaign", fake_onboard)
    sentinel: list[str] = []

    def on_progress(msg: str) -> None:
        sentinel.append(msg)

    await _runtime.start_neurocomment("listener-1", on_progress=on_progress)

    assert seen == [on_progress]


@pytest.mark.asyncio
async def test_stop_shuts_down_persisted_listener_then_clears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _ReconcileSpy()
    _patch_engine(monkeypatch, spy)
    await set_listener_account_id("listener-1")

    await _runtime.stop_neurocomment()

    assert spy.shut_down == ["listener-1"]
    assert await get_listener_account_id() is None


@pytest.mark.asyncio
async def test_stop_with_no_listener_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _ReconcileSpy()
    _patch_engine(monkeypatch, spy)

    await _runtime.stop_neurocomment()

    assert spy.shut_down == []
    assert await get_listener_account_id() is None


@pytest.mark.asyncio
async def test_reconcile_on_startup_resumes_persisted_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _ReconcileSpy()
    _patch_engine(monkeypatch, spy)
    await set_listener_account_id("listener-1")

    await _runtime.reconcile_neurocomment_on_startup()

    assert spy.reconciled == ["listener-1"]


@pytest.mark.asyncio
async def test_reconcile_on_startup_does_nothing_when_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _ReconcileSpy()
    _patch_engine(monkeypatch, spy)

    await _runtime.reconcile_neurocomment_on_startup()

    assert spy.reconciled == []


@pytest.mark.asyncio
async def test_shutdown_on_shutdown_tears_down_persisted_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _ReconcileSpy()
    _patch_engine(monkeypatch, spy)
    await set_listener_account_id("listener-1")

    await _runtime.shutdown_neurocomment_on_shutdown()

    assert spy.shut_down == ["listener-1"]


@pytest.mark.asyncio
async def test_shutdown_on_shutdown_does_nothing_when_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _ReconcileSpy()
    _patch_engine(monkeypatch, spy)

    await _runtime.shutdown_neurocomment_on_shutdown()

    assert spy.shut_down == []


# --------------------------------------------------------------------------- #
# Deletion sweep (#131): periodic re-read → escalating channel back-off.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_reconcile_starts_sweep_and_shutdown_cancels_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, "@a")
    _patch_listener(monkeypatch, _ListenerSpy())
    _patch_execute(monkeypatch, _ExecuteSpy())

    await _runtime.reconcile_neurocomment_runtime("listener-1")
    try:
        assert _runtime._SWEEP_TASK is not None
        assert not _runtime._SWEEP_TASK.done()
    finally:
        await _runtime.shutdown_neurocomment_runtime("listener-1")

    assert _runtime._SWEEP_TASK is None


@pytest.mark.asyncio
async def test_reconcile_with_no_channels_does_not_start_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_listener(monkeypatch, _ListenerSpy())

    await _runtime.reconcile_neurocomment_runtime("listener-1")

    assert _runtime._SWEEP_TASK is None


async def _campaign_with_posted_comments(channel: str, msg_ids: list[int]) -> None:
    """Active campaign on ``channel`` with one ``posted`` comment per ``msg_ids`` entry."""
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, channel)
    await create_account(AccountCreate(account_id="acc-1", label="acc-1", session_name="acc-1"))
    for post_id, msg_id in enumerate(msg_ids, start=1):
        await claim_comment(channel, post_id, campaign.campaign_id, "acc-1")
        await mark_comment_posted(channel, post_id, comment_text="x", comment_msg_id=msg_id)


@pytest.mark.asyncio
async def test_sweep_trips_backoff_when_deletions_reach_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.neurocomment, "channel_backoff_min_deletions", 2)
    await _campaign_with_posted_comments("@a", [101, 102, 103])

    async def fake_read(_account_id: str, action: CheckMessagesAlive) -> CheckMessagesAliveResult:
        # Two of the three comments have vanished — at the threshold.
        gone = [mid for mid in action.message_ids if mid in (101, 102)]
        return CheckMessagesAliveResult(missing_ids=gone)

    monkeypatch.setattr("services.neurocomment._seams.execute_read", fake_read)

    await _runtime._sweep_once()

    assert _state.channel_in_backoff("@a", datetime.now(UTC)) is True


@pytest.mark.asyncio
async def test_sweep_below_threshold_does_not_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.neurocomment, "channel_backoff_min_deletions", 3)
    await _campaign_with_posted_comments("@a", [101, 102, 103])

    async def fake_read(_account_id: str, _action: CheckMessagesAlive) -> CheckMessagesAliveResult:
        return CheckMessagesAliveResult(missing_ids=[101])  # one gone, below threshold 3

    monkeypatch.setattr("services.neurocomment._seams.execute_read", fake_read)

    await _runtime._sweep_once()

    assert _state.channel_in_backoff("@a", datetime.now(UTC)) is False


@pytest.mark.asyncio
async def test_sweep_read_failure_does_not_trip_or_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.neurocomment, "channel_backoff_min_deletions", 1)
    await _campaign_with_posted_comments("@a", [101, 102])

    async def boom(_account_id: str, _action: CheckMessagesAlive) -> CheckMessagesAliveResult:
        msg = "read failed"
        raise RuntimeError(msg)

    monkeypatch.setattr("services.neurocomment._seams.execute_read", boom)

    await _runtime._sweep_once()  # one channel's read failure must not abort the sweep

    assert _state.channel_in_backoff("@a", datetime.now(UTC)) is False


@pytest.mark.asyncio
async def test_sweep_disabled_when_interval_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.neurocomment, "deletion_sweep_interval_seconds", 0.0)
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, "@a")
    _patch_listener(monkeypatch, _ListenerSpy())
    _patch_execute(monkeypatch, _ExecuteSpy())

    await _runtime.reconcile_neurocomment_runtime("listener-1")
    try:
        assert _runtime._SWEEP_TASK is None  # sweep disabled by config
    finally:
        await _runtime.shutdown_neurocomment_runtime("listener-1")


@pytest.mark.asyncio
async def test_sweep_does_not_re_escalate_while_cooled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.neurocomment, "channel_backoff_min_deletions", 2)
    await _campaign_with_posted_comments("@a", [101, 102, 103])

    reads = 0

    async def fake_read(_account_id: str, action: CheckMessagesAlive) -> CheckMessagesAliveResult:
        nonlocal reads
        reads += 1
        return CheckMessagesAliveResult(missing_ids=list(action.message_ids))  # all gone

    monkeypatch.setattr("services.neurocomment._seams.execute_read", fake_read)

    await _runtime._sweep_once()  # trips once
    await _runtime._sweep_once()  # already cooled → skipped: no re-read, no re-escalation

    assert _state.channel_in_backoff("@a", datetime.now(UTC)) is True
    assert _state._CHANNEL_TRIPS["@a"] == 1  # escalated exactly once, not per sweep
    assert reads == 1  # the second sweep skipped the gateway read entirely
