"""Tests for neurocomment runtime listener behavior."""

from __future__ import annotations

import asyncio

import pytest

from core.config import settings
from core.db import (
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
    await _runtime.reconcile_neurocomment_runtime("listener-1")

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
    exec_spy.ok = True
    await _runtime.reconcile_neurocomment_runtime("listener-1")  # retried → ok → cached
    await _runtime.reconcile_neurocomment_runtime("listener-1")  # cached → skipped

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

    # 3 fresh joins → 2 inter-join pauses, each the jittered value. (Other 42.0-free
    # sleeps, e.g. the deletion sweep interval, are unrelated and ignored.)
    assert [s for s in sleeps if s == 42.0] == [42.0, 42.0]
    # Second reconcile: every channel is now cached in _JOINED_CHANNELS, so no join
    # RPCs fire and no inter-join pause runs — cache-hits must never sleep.
    sleeps.clear()
    await _runtime.reconcile_neurocomment_runtime("listener-1")
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

    # Cap of 1: one join lands, then the loop breaks before the next.
    assert len(exec_spy.joined) == 1
    # The listener still subscribes to every watched channel after the break.
    assert set(spy.subscribed[0][1]) == {"@a", "@b", "@c"}
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
