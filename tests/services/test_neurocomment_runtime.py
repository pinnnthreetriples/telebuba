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
    get_listener_running,
    link_channel_to_campaign,
    mark_comment_posted,
    set_listener_account_id,
    set_listener_running,
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
    await set_listener_running(running=True)

    status = await _runtime.neurocomment_runtime_status()

    assert status.running is True
    assert status.active_channels == 2
    assert status.listener_account_id == "listener-1"


@pytest.mark.asyncio
async def test_runtime_status_carries_log_limit_from_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The activity-log row cap is served from config so the SPA stops hardcoding it (#7)."""
    monkeypatch.setattr(settings.neurocomment, "log_limit", 42)

    stopped = await _runtime.neurocomment_runtime_status()
    assert stopped.running is False
    assert stopped.log_limit == 42

    await set_listener_account_id("listener-1")
    await set_listener_running(running=True)
    running = await _runtime.neurocomment_runtime_status()
    assert running.running is True
    assert running.log_limit == 42


@pytest.mark.asyncio
async def test_runtime_status_running_with_no_channels_reports_zero() -> None:
    # A running listener with an empty watch set still reads as running (the
    # listener is up); the count is simply 0.
    await set_listener_account_id("listener-1")
    await set_listener_running(running=True)

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
    assert await get_listener_running() is True
    assert spy.reconciled == ["listener-1"]


async def _drain_onboarding() -> None:
    """Await the background onboarding task Start scheduled (if any), then clear it."""
    task = _runtime._ONBOARD_TASK
    if task is not None:
        await task


@pytest.mark.asyncio
async def test_start_returns_promptly_and_schedules_background_onboarding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Start persists+reconciles and returns without awaiting onboarding (#4).

    The POST no longer blocks on minutes of jittered join/challenge sleeps; onboarding
    runs as a tracked background task. Progress is observable over the SSE log stream.
    """
    active_a = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    active_b = await create_campaign(CampaignCreate(name="B", prompt="p", status="active"))
    paused = await create_campaign(CampaignCreate(name="C", prompt="p", status="paused"))
    spy = _ReconcileSpy()
    _patch_engine(monkeypatch, spy)
    onboard_started = asyncio.Event()
    release = asyncio.Event()
    onboarded: list[str] = []

    async def slow_onboard(campaign_id: str, **_kwargs: object) -> object:
        onboard_started.set()
        await release.wait()  # block so Start would time out if it awaited us
        onboarded.append(campaign_id)
        return None

    monkeypatch.setattr(_runtime, "onboard_campaign", slow_onboard)

    # Start returns promptly even though onboarding blocks.
    await asyncio.wait_for(_runtime.start_neurocomment("listener-1"), timeout=0.5)

    # Listener persisted + reconciled synchronously; onboarding scheduled, not awaited.
    assert await get_listener_account_id() == "listener-1"
    assert spy.reconciled == ["listener-1"]
    assert _runtime._ONBOARD_TASK is not None
    await asyncio.wait_for(onboard_started.wait(), timeout=0.5)
    assert onboarded == []  # still blocked → Start did not wait for it

    # Let it finish: both active campaigns onboarded, the paused one skipped.
    release.set()
    await _drain_onboarding()
    assert set(onboarded) == {active_a.campaign_id, active_b.campaign_id}
    assert paused.campaign_id not in onboarded


@pytest.mark.asyncio
async def test_start_stops_previous_listener_when_account_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Starting with account B after A must stop A's listener, or both get every post (#3)."""
    spy = _ListenerSpy()
    _patch_listener(monkeypatch, spy)
    reconcile = _ReconcileSpy()
    monkeypatch.setattr(_runtime, "reconcile_neurocomment_runtime", reconcile.reconcile)

    async def _noop_onboard(_campaign_id: str, **_kwargs: object) -> object:
        return None

    monkeypatch.setattr(_runtime, "onboard_campaign", _noop_onboard)

    await set_listener_account_id("acc-A")
    await _runtime.start_neurocomment("acc-B")
    await _drain_onboarding()

    # The previous account's per-account subscription is torn down before B is wired.
    assert spy.stopped == ["acc-A"]
    assert reconcile.reconciled == ["acc-B"]
    assert await get_listener_account_id() == "acc-B"


@pytest.mark.asyncio
async def test_start_same_account_does_not_stop_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-Start on the same account must not tear down its own subscription (#3)."""
    spy = _ListenerSpy()
    _patch_listener(monkeypatch, spy)
    monkeypatch.setattr(_runtime, "reconcile_neurocomment_runtime", _ReconcileSpy().reconcile)

    async def _noop_onboard(_campaign_id: str, **_kwargs: object) -> object:
        return None

    monkeypatch.setattr(_runtime, "onboard_campaign", _noop_onboard)

    await set_listener_account_id("acc-A")
    await _runtime.start_neurocomment("acc-A")
    await _drain_onboarding()

    assert spy.stopped == []


@pytest.mark.asyncio
async def test_start_continues_to_next_campaign_when_one_onboard_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One campaign's background onboarding raise must not blackhole the others.

    The listener still gets persisted and reconcile still fires (synchronously in
    Start), and the remaining campaigns still onboard.
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
    await _drain_onboarding()

    assert set(onboarded) == {a.campaign_id, b.campaign_id}
    assert await get_listener_account_id() == "listener-1"
    assert spy.reconciled == ["listener-1"]


@pytest.mark.asyncio
async def test_start_passes_on_progress_through_to_onboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """start_neurocomment forwards on_progress to the background onboard_campaign."""
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
    await _drain_onboarding()

    assert seen == [on_progress]


@pytest.mark.asyncio
async def test_rapid_second_start_does_not_spawn_duplicate_onboarding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two rapid Starts must not run onboarding twice concurrently (#4)."""
    await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    _patch_engine(monkeypatch, _ReconcileSpy())
    release = asyncio.Event()
    runs = 0

    async def slow_onboard(_campaign_id: str, **_kwargs: object) -> object:
        nonlocal runs
        runs += 1
        await release.wait()
        return None

    monkeypatch.setattr(_runtime, "onboard_campaign", slow_onboard)

    await _runtime.start_neurocomment("listener-1")
    first_task = _runtime._ONBOARD_TASK
    await asyncio.sleep(0)  # let the first onboarding task start + block
    # Second Start while the first onboarding is still in flight reuses it.
    await _runtime.start_neurocomment("listener-1")
    assert _runtime._ONBOARD_TASK is first_task

    release.set()
    await _drain_onboarding()
    assert runs == 1  # onboarding ran once, not twice


@pytest.mark.asyncio
async def test_shutdown_cancels_background_onboarding(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shutdown cancels the in-flight onboarding task cleanly (#4)."""
    await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    # Patch only reconcile (Start uses it); keep the REAL shutdown so _stop_onboarding runs.
    monkeypatch.setattr(_runtime, "reconcile_neurocomment_runtime", _ReconcileSpy().reconcile)
    _patch_listener(monkeypatch, _ListenerSpy())
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def never_ending(_campaign_id: str, **_kwargs: object) -> object:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(_runtime, "onboard_campaign", never_ending)

    await _runtime.start_neurocomment("listener-1")
    await asyncio.wait_for(started.wait(), timeout=0.5)

    await _runtime.shutdown_neurocomment_runtime("listener-1")

    assert cancelled.is_set()
    assert _runtime._ONBOARD_TASK is None


@pytest.mark.asyncio
async def test_stop_shuts_down_persisted_listener_then_clears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _ReconcileSpy()
    _patch_engine(monkeypatch, spy)
    await set_listener_account_id("listener-1")
    await set_listener_running(running=True)

    await _runtime.stop_neurocomment()

    # PAUSE: unsubscribed + run flag cleared, but the listener is REMEMBERED so the
    # SPA keeps the strip after a reload (this is what distinguishes pause from remove).
    assert spy.shut_down == ["listener-1"]
    assert await get_listener_account_id() == "listener-1"
    assert await get_listener_running() is False


@pytest.mark.asyncio
async def test_stop_with_no_listener_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _ReconcileSpy()
    _patch_engine(monkeypatch, spy)

    await _runtime.stop_neurocomment()

    assert spy.shut_down == []
    assert await get_listener_account_id() is None
    assert await get_listener_running() is False


@pytest.mark.asyncio
async def test_clear_listener_wipes_id_and_run_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clearing the listener removes the account entirely (unlike pause, which keeps it)."""
    spy = _ReconcileSpy()
    _patch_engine(monkeypatch, spy)
    await set_listener_account_id("listener-1")
    await set_listener_running(running=True)

    await _runtime.clear_neurocomment_listener()

    assert spy.shut_down == ["listener-1"]
    assert await get_listener_account_id() is None
    assert await get_listener_running() is False


@pytest.mark.asyncio
async def test_clear_listener_with_none_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _ReconcileSpy()
    _patch_engine(monkeypatch, spy)

    await _runtime.clear_neurocomment_listener()

    assert spy.shut_down == []
    assert await get_listener_account_id() is None
    assert await get_listener_running() is False


@pytest.mark.asyncio
async def test_status_after_pause_keeps_remembered_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After pause the status shows the remembered listener with running False (audit fix)."""
    spy = _ReconcileSpy()
    _patch_engine(monkeypatch, spy)
    await set_listener_account_id("listener-1")
    await set_listener_running(running=True)

    await _runtime.stop_neurocomment()
    status = await _runtime.neurocomment_runtime_status()

    assert status.running is False
    assert status.listener_account_id == "listener-1"


@pytest.mark.asyncio
async def test_reconcile_if_running_gates_on_persisted_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """reconcile_if_running re-points only when the runtime is running (gated on the flag)."""
    spy = _ReconcileSpy()
    monkeypatch.setattr(_runtime, "reconcile_neurocomment_runtime", spy.reconcile)

    # Stopped: no listener persisted → no-op.
    await _runtime.reconcile_if_running()
    assert spy.reconciled == []

    # Paused: listener remembered but the run flag is off → still a no-op.
    await set_listener_account_id("listener-1")
    await _runtime.reconcile_if_running()
    assert spy.reconciled == []

    # Running: the run flag is set → reconcile the remembered account.
    await set_listener_running(running=True)
    await _runtime.reconcile_if_running()
    assert spy.reconciled == ["listener-1"]


@pytest.mark.asyncio
async def test_reconcile_on_startup_resumes_persisted_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _ReconcileSpy()
    _patch_engine(monkeypatch, spy)
    await set_listener_account_id("listener-1")
    await set_listener_running(running=True)

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
async def test_reconcile_on_startup_does_not_resume_paused_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A remembered-but-paused listener must NOT auto-resume on boot (audit fix).

    Pausing then rebooting leaves the account remembered with the run flag off;
    reconcile-on-startup gates on the flag, so nothing is resubscribed.
    """
    spy = _ReconcileSpy()
    _patch_engine(monkeypatch, spy)
    await set_listener_account_id("listener-1")
    await set_listener_running(running=True)
    await _runtime.stop_neurocomment()  # pause: keeps id, clears the flag
    spy.reconciled.clear()

    await _runtime.reconcile_neurocomment_on_startup()

    assert spy.reconciled == []
    assert await get_listener_account_id() == "listener-1"


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
