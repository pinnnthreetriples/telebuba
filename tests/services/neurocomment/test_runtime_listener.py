"""Tests for neurocomment runtime listener behavior."""

from __future__ import annotations

import asyncio

import pytest

from core.config import settings
from core.db import (
    count_account_joins_since,
    create_campaign,
    get_listener_account_id,
    get_listener_running,
    link_channel_to_campaign,
    set_listener_account_id,
    set_listener_running,
)
from schemas.neurocomment import CampaignCreate
from schemas.telegram_actions import (
    ActionResult,
    ActionStatus,
    JoinChannel,
    NewPostEvent,
)
from services.neurocomment import _runtime
from tests.services.neurocomment.runtime_support import (
    _drain_joins,
    _ExecuteSpy,
    _ListenerSpy,
    _patch_execute,
    _patch_listener,
    _patch_warming_ids,
)

pytestmark = pytest.mark.usefixtures("isolate_runtime")


@pytest.mark.asyncio
async def test_start_rejects_listener_that_is_warming(monkeypatch: pytest.MonkeyPatch) -> None:
    # An actively-warming account must not double as the listener; the guard runs
    # before anything is persisted.
    _patch_warming_ids(monkeypatch, {"listener-1"})
    with pytest.raises(_runtime.ListenerBusyWarmingError):
        await _runtime.start_neurocomment("listener-1")
    assert await get_listener_account_id() is None
    assert await get_listener_running() is False


@pytest.mark.asyncio
async def test_start_allows_listener_that_is_not_warming(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _ListenerSpy()
    _patch_listener(monkeypatch, spy)
    monkeypatch.setattr(_runtime, "_ensure_onboarding_running", lambda *a, **k: None)  # noqa: ARG005
    # A different account is warming; the picked listener is free, so start proceeds.
    _patch_warming_ids(monkeypatch, {"other"})
    await _runtime.start_neurocomment("listener-2")
    assert await get_listener_account_id() == "listener-2"
    assert await get_listener_running() is True


@pytest.mark.asyncio
async def test_reconcile_unsubscribes_a_warming_listener(monkeypatch: pytest.MonkeyPatch) -> None:
    # The guard lives at the reconcile choke point too, so a persisted listener that
    # is warming is stopped (never re-subscribed) on startup/channel-edit resume,
    # even when there are active channels to watch.
    spy = _ListenerSpy()
    _patch_listener(monkeypatch, spy)
    _patch_warming_ids(monkeypatch, {"listener-1"})
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, "@a")

    await _runtime.reconcile_neurocomment_runtime("listener-1")

    assert spy.subscribed == []
    assert spy.stopped == ["listener-1"]


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
async def test_runtime_status_reports_onboarding_in_flight() -> None:
    """The live onboarding flag comes from the real task handle, not a heuristic.

    The SPA animates the board on this: a slow jittered onboarding must read as
    "working", not "no data".
    """
    assert (await _runtime.neurocomment_runtime_status()).onboarding is False

    release = asyncio.Event()

    async def _hold() -> None:
        await release.wait()

    _runtime._ONBOARD_TASK = asyncio.create_task(_hold())
    try:
        assert _runtime.is_onboarding_running() is True
        assert (await _runtime.neurocomment_runtime_status()).onboarding is True
    finally:
        release.set()
        await _runtime._ONBOARD_TASK

    assert (await _runtime.neurocomment_runtime_status()).onboarding is False


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

    # Subscribe happens synchronously; the paced joins run in the background task.
    assert len(spy.subscribed) == 1
    account_id, channels = spy.subscribed[0]
    assert account_id == "listener-1"
    assert set(channels) == {"@a", "@b"}
    assert spy.stopped == []
    await _drain_joins()
    # The listener account is joined to every watched channel.
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

    # A failed join must not stop the listener from subscribing (subscribe is now
    # synchronous and happens regardless of how the background joins fare).
    assert len(spy.subscribed) == 1
    assert spy.subscribed[0][1] == ["@a"]
    await _drain_joins()
    await _runtime.shutdown_neurocomment_runtime("listener-1")


@pytest.mark.asyncio
async def test_reconcile_joins_each_channel_once_per_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated reconciles (one per channel link) must not re-join joined channels.

    Ten rapid channel links used to re-run the full join sweep each time — dozens
    of JoinChannel RPCs in seconds, a real Telegram flood risk on the listener.
    """
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, "@a")
    await link_channel_to_campaign(campaign.campaign_id, "@b")
    _patch_listener(monkeypatch, _ListenerSpy())
    exec_spy = _ExecuteSpy()
    _patch_execute(monkeypatch, exec_spy)

    await _runtime.reconcile_neurocomment_runtime("listener-1")
    await _drain_joins()
    await _runtime.reconcile_neurocomment_runtime("listener-1")
    await _drain_joins()

    # One join per channel across both reconciles, not per call.
    assert len(exec_spy.joined) == 2
    assert {ch for _aid, ch in exec_spy.joined} == {"@a", "@b"}
    await _runtime.shutdown_neurocomment_runtime("listener-1")


@pytest.mark.asyncio
async def test_reconcile_retries_failed_join_on_next_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only an ok join is cached — a failed join is retried on the next reconcile."""
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, "@a")
    _patch_listener(monkeypatch, _ListenerSpy())
    exec_spy = _ExecuteSpy(ok=False)
    _patch_execute(monkeypatch, exec_spy)

    await _runtime.reconcile_neurocomment_runtime("listener-1")  # join fails → not cached
    await _drain_joins()
    exec_spy.ok = True
    await _runtime.reconcile_neurocomment_runtime("listener-1")  # retried → ok → cached
    await _drain_joins()
    await _runtime.reconcile_neurocomment_runtime("listener-1")  # cached → skipped
    await _drain_joins()

    assert exec_spy.joined == [("listener-1", "@a"), ("listener-1", "@a")]
    await _runtime.shutdown_neurocomment_runtime("listener-1")


@pytest.mark.asyncio
async def test_reconcile_paces_joins_with_jittered_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Listener joins are spaced by a jittered pause (never one burst = freeze vector).

    Pause runs *between* actual joins only: no pause before the first, none after
    the last, so N fresh channels produce N-1 pauses.
    """
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, "@a")
    await link_channel_to_campaign(campaign.campaign_id, "@b")
    await link_channel_to_campaign(campaign.campaign_id, "@c")
    _patch_listener(monkeypatch, _ListenerSpy())
    _patch_execute(monkeypatch, _ExecuteSpy())
    monkeypatch.setattr(_runtime, "_join_jitter_seconds", lambda: 42.0)
    sleeps: list[float] = []

    async def _record(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(_runtime.asyncio, "sleep", _record)

    await _runtime.reconcile_neurocomment_runtime("listener-1")
    await _drain_joins()  # the pacing now happens in the background join task

    # 3 fresh joins → 2 inter-join pauses, each the jittered value. (Other 42.0-free
    # sleeps, e.g. the deletion sweep interval, are unrelated and ignored.)
    assert [s for s in sleeps if s == 42.0] == [42.0, 42.0]
    # Second reconcile: every channel is now cached in _JOINED_CHANNELS, so no join
    # RPCs fire and no inter-join pause runs — cache-hits must never sleep.
    sleeps.clear()
    await _runtime.reconcile_neurocomment_runtime("listener-1")
    await _drain_joins()
    assert [s for s in sleeps if s == 42.0] == []
    await _runtime.shutdown_neurocomment_runtime("listener-1")


@pytest.mark.asyncio
async def test_reconcile_stops_join_burst_on_flood(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A flood/rate-limit status halts the join loop instead of firing more RPCs.

    Escalating a soft flood-wait into a hard freeze is exactly what got accounts
    frozen; the remaining channels retry on the next reconcile.
    """
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    for ch in ("@a", "@b", "@c"):
        await link_channel_to_campaign(campaign.campaign_id, ch)
    spy = _ListenerSpy()
    _patch_listener(monkeypatch, spy)
    attempts: list[str] = []

    async def _ok_then_flood(account_id: str, action: JoinChannel) -> ActionResult:
        # First join succeeds; the next one floods mid-loop.
        attempts.append(action.channel)
        status: ActionStatus = "ok" if len(attempts) == 1 else "flood_wait"
        return ActionResult(status=status, action_type=action.action_type, account_id=account_id)

    monkeypatch.setattr("services.neurocomment._seams.execute", _ok_then_flood)

    await _runtime.reconcile_neurocomment_runtime("listener-1")
    await _drain_joins()  # flood/pacing now enforced inside the background join task

    # Broke after the flood on the 2nd join → the 3rd channel is never attempted.
    assert len(attempts) == 2
    # The successful join is cached; the flooded one is not (retried next reconcile).
    assert ("listener-1", attempts[0]) in _runtime._JOINED_CHANNELS
    assert ("listener-1", attempts[1]) not in _runtime._JOINED_CHANNELS
    # Valid state after the break: the listener still subscribes to all channels.
    assert set(spy.subscribed[0][1]) == {"@a", "@b", "@c"}
    await _runtime.shutdown_neurocomment_runtime("listener-1")


@pytest.mark.asyncio
async def test_reconcile_stops_joining_once_listener_at_daily_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The single listener account stops joining once it hits its rolling-24h cap.

    Remaining channels retry on the next reconcile as the window rolls; the listener
    still subscribes to the full watch set (mirrors the flood-guard break).
    """
    monkeypatch.setattr(settings.neurocomment, "max_joins_per_account_per_day", 1)
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    for ch in ("@a", "@b", "@c"):
        await link_channel_to_campaign(campaign.campaign_id, ch)
    spy = _ListenerSpy()
    _patch_listener(monkeypatch, spy)
    exec_spy = _ExecuteSpy()
    _patch_execute(monkeypatch, exec_spy)

    await _runtime.reconcile_neurocomment_runtime("listener-1")
    await _drain_joins()  # the cap gate now runs inside the background join task

    # Cap of 1: one join lands, then the loop breaks before the next.
    assert len(exec_spy.joined) == 1
    # The listener still subscribes to every watched channel after the break.
    assert set(spy.subscribed[0][1]) == {"@a", "@b", "@c"}
    await _runtime.shutdown_neurocomment_runtime("listener-1")


@pytest.mark.asyncio
async def test_reconcile_caches_already_participant_without_recording_join(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An already-participant channel is cached (joined) but must not consume cap budget.

    On a listener restart the whole watch set re-joins as already-participant; recording
    those no-ops would pin the count near the cap and starve genuinely-new joins.
    """
    monkeypatch.setattr(settings.neurocomment, "max_joins_per_account_per_day", 20)
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, "@a")
    spy = _ListenerSpy()
    _patch_listener(monkeypatch, spy)

    async def _already(account_id: str, action: JoinChannel) -> ActionResult:
        return ActionResult(
            status="already_participant",
            action_type=action.action_type,
            account_id=account_id,
        )

    monkeypatch.setattr("services.neurocomment._seams.execute", _already)

    await _runtime.reconcile_neurocomment_runtime("listener-1")
    await _drain_joins()

    # It IS joined → cached so we stop re-joining, and the listener subscribes.
    assert ("listener-1", "@a") in _runtime._JOINED_CHANNELS
    assert spy.subscribed[0][1] == ["@a"]
    # But the no-op re-join is not recorded against the rolling-24h cap.
    assert await count_account_joins_since("listener-1", "1970-01-01T00:00:00+00:00") == 0
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
async def test_on_post_drops_when_at_capacity(monkeypatch: pytest.MonkeyPatch) -> None:
    """At the concurrency cap, further posts are dropped (not spawned) — flood protection (L4)."""
    monkeypatch.setattr(settings.neurocomment, "max_concurrent_post_tasks", 2)
    release = asyncio.Event()

    async def blocking_handle(_event: NewPostEvent) -> None:
        await release.wait()

    monkeypatch.setattr(_runtime, "handle_new_post", blocking_handle)

    for post_id in range(5):
        await _runtime.on_post(NewPostEvent(channel="@a", post_id=post_id, text="hi"))

    # Only the first two spawned; the remaining three were dropped at capacity.
    assert len(_runtime._TASKS) == 2

    release.set()
    await asyncio.gather(*list(_runtime._TASKS), return_exceptions=True)


@pytest.mark.asyncio
async def test_on_post_accepts_up_to_capacity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Posts up to the cap all spawn — the bound never drops below capacity (L4)."""
    monkeypatch.setattr(settings.neurocomment, "max_concurrent_post_tasks", 3)
    release = asyncio.Event()

    async def blocking_handle(_event: NewPostEvent) -> None:
        await release.wait()

    monkeypatch.setattr(_runtime, "handle_new_post", blocking_handle)

    for post_id in range(3):
        await _runtime.on_post(NewPostEvent(channel="@a", post_id=post_id, text="hi"))

    assert len(_runtime._TASKS) == 3

    release.set()
    await asyncio.gather(*list(_runtime._TASKS), return_exceptions=True)


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


@pytest.mark.asyncio
async def test_reconcile_returns_before_paced_joins_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reconcile (and thus Start) returns before the paced joins finish — the fix.

    The paced join loop used to run inline, blocking the caller (and, via Start, the
    per-account lock) for ~minutes. It now runs as a background task, so reconcile
    subscribes and returns while the joins are still pacing.
    """
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    for ch in ("@a", "@b", "@c"):
        await link_channel_to_campaign(campaign.campaign_id, ch)
    spy = _ListenerSpy()
    _patch_listener(monkeypatch, spy)
    exec_spy = _ExecuteSpy()
    _patch_execute(monkeypatch, exec_spy)
    # A real, small, positive jitter so the joins can't all finish synchronously.
    monkeypatch.setattr(_runtime, "_join_jitter_seconds", lambda: 0.05)

    await _runtime.reconcile_neurocomment_runtime("listener-1")

    # Subscribe already happened synchronously, but the paced joins are still in flight.
    assert len(spy.subscribed) == 1
    assert len(exec_spy.joined) < 3
    assert _runtime._JOIN_TASK is not None
    assert not _runtime._JOIN_TASK.done()

    await _drain_joins()  # let the background task finish pacing
    assert {ch for _aid, ch in exec_spy.joined} == {"@a", "@b", "@c"}
    await _runtime.shutdown_neurocomment_runtime("listener-1")


@pytest.mark.asyncio
async def test_second_reconcile_while_joining_coalesces_and_adds_new_channels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Single-flight coalescing: a mid-pace reconcile reruns the SAME task.

    A reconcile that arrives while a pass is in flight queues one rerun on the same
    task instead of spawning a second pacer, and the rerun re-reads the watch set so
    a channel linked mid-pace is joined too.
    """
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, "@a")
    await link_channel_to_campaign(campaign.campaign_id, "@b")
    _patch_listener(monkeypatch, _ListenerSpy())
    gate = asyncio.Event()
    attempts: list[str] = []

    async def _gated(account_id: str, action: JoinChannel) -> ActionResult:
        attempts.append(action.channel)
        await gate.wait()  # park inside the pass so a 2nd reconcile lands mid-flight
        return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)

    monkeypatch.setattr("services.neurocomment._seams.execute", _gated)

    await _runtime.reconcile_neurocomment_runtime("listener-1")
    await asyncio.sleep(0)  # let the task reach the gate on the first join
    first_task = _runtime._JOIN_TASK

    # Link a third channel and reconcile again while the first pass is still parked.
    await link_channel_to_campaign(campaign.campaign_id, "@c")
    await _runtime.reconcile_neurocomment_runtime("listener-1")

    # Single-flight: no second pacer spawned; the in-flight one is flagged to rerun.
    assert _runtime._JOIN_TASK is first_task
    assert _runtime._JOIN_RERUN is True

    gate.set()
    await _drain_joins()
    # The coalesced rerun re-read the watch set → @c joined too, each channel once.
    assert set(attempts) == {"@a", "@b", "@c"}
    await _runtime.shutdown_neurocomment_runtime("listener-1")


@pytest.mark.asyncio
async def test_concurrent_reconciles_do_not_pace_in_parallel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A burst of reconciles never spawns parallel pacers (single-flight).

    Concurrent reconciles each pacing independently is the exact join burst the pacing
    guards against; single-flight collapses them to one stream, one join per channel.
    """
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    for ch in ("@a", "@b", "@c"):
        await link_channel_to_campaign(campaign.campaign_id, ch)
    _patch_listener(monkeypatch, _ListenerSpy())
    exec_spy = _ExecuteSpy()
    _patch_execute(monkeypatch, exec_spy)
    monkeypatch.setattr(_runtime, "_join_jitter_seconds", lambda: 0.02)

    # Fire several reconciles in a burst while the first pass is still pacing.
    for _ in range(5):
        await _runtime.reconcile_neurocomment_runtime("listener-1")
    await _drain_joins()

    # One pacing stream, one join per channel — not five overlapping bursts.
    assert {ch for _aid, ch in exec_spy.joined} == {"@a", "@b", "@c"}
    assert len(exec_spy.joined) == 3
    await _runtime.shutdown_neurocomment_runtime("listener-1")
