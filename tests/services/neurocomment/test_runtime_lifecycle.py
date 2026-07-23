"""Tests for neurocomment runtime lifecycle behavior."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core import events
from core.db import (
    _get_engine,
    assign_account_to_campaign,
    claim_comment,
    create_account,
    create_campaign,
    fetch_comment,
    get_listener_account_id,
    get_listener_running,
    link_channel_to_campaign,
    list_recent_logs,
    set_listener_account_id,
    set_listener_running,
)
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate
from schemas.spam_status import SpamStatusVerdict
from schemas.telegram_actions import (
    BotChallengeWaitResult,
    LinkedDiscussionGroupResult,
    WaitForBotChallenge,
)
from services.neurocomment import _runtime, _seams, onboarding

if TYPE_CHECKING:
    from schemas.neurocomment_progress import OnboardingProgressEvent


from tests.services.neurocomment.runtime_support import (
    _ExecuteSpy,
    _ListenerSpy,
    _patch_execute,
    _patch_listener,
)

pytestmark = pytest.mark.usefixtures("isolate_runtime")

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
    sentinel: list[object] = []

    def on_progress(event: OnboardingProgressEvent) -> None:
        sentinel.append(event)

    await _runtime.start_neurocomment("listener-1", on_progress=on_progress)
    await _drain_onboarding()

    assert seen == [on_progress]


@pytest.mark.asyncio
async def test_onboarding_emits_transient_progress_frames_without_persisting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Onboarding nudges the SSE bus so the board refreshes live — with NO log row.

    The transient frame replaced the FE's 4s onboarding poll: it must reach the bus
    (drives the SPA's invalidate → board refetch) yet never be persisted, or the
    event log would flood with a frame per channel-join.
    """
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, "@a")
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")

    _patch_listener(monkeypatch, _ListenerSpy())
    _patch_execute(monkeypatch, _ExecuteSpy())  # reconcile + onboarding joins → ok

    async def _resolve(_account_id: str, action: object) -> object:
        if isinstance(action, WaitForBotChallenge):
            return BotChallengeWaitResult(message=None)
        return LinkedDiscussionGroupResult(linked_chat_id=500, comments_enabled=True)

    async def _clean_spam(account_id: str, **_kwargs: object) -> SpamStatusVerdict:
        return SpamStatusVerdict(
            account_id=account_id, status="clean", checked_at="2026-01-01T00:00:00"
        )

    monkeypatch.setattr(_seams, "execute_read", _resolve)
    monkeypatch.setattr(_seams, "refresh_spam_status", _clean_spam)
    monkeypatch.setattr(onboarding.asyncio, "sleep", _no_sleep([]))

    async with events.subscribe() as queue:
        await _runtime.start_neurocomment("listener-1")
        await _drain_onboarding()
        frames: list[object] = []
        with suppress(asyncio.QueueEmpty):
            while True:
                frames.append(queue.get_nowait())

    transient = [f for f in frames if f.event == "neurocomment_onboarding_progress"]
    assert transient, "onboarding must nudge the bus with a transient progress frame"
    assert all(f.id == 0 for f in transient)  # synthetic, never a persisted row
    # The nudge is deliberately absent from the logs table (no event-log flooding).
    logs = await list_recent_logs(limit=200)
    assert all(row.event != "neurocomment_onboarding_progress" for row in logs)


def _no_sleep(records: list[float]) -> object:
    async def _sleep(seconds: float) -> None:
        records.append(seconds)

    return _sleep


@pytest.mark.asyncio
async def test_rapid_second_start_does_not_spawn_duplicate_onboarding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two rapid Starts must not run onboarding twice concurrently (#4).

    The second Start reuses the in-flight task and queues one coalesced rerun,
    so its trigger is deferred — never a concurrent duplicate.
    """
    await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    _patch_engine(monkeypatch, _ReconcileSpy())
    started = asyncio.Event()
    release = asyncio.Event()
    runs = 0

    async def slow_onboard(_campaign_id: str, **_kwargs: object) -> object:
        nonlocal runs
        runs += 1
        if runs == 1:
            started.set()
            await release.wait()
        return None

    monkeypatch.setattr(_runtime, "onboard_campaign", slow_onboard)

    await _runtime.start_neurocomment("listener-1")
    first_task = _runtime._ONBOARD_TASK
    # Deterministically wait for the first pass to begin + block (an unconditional
    # sleep(0) races the to_thread DB read on a loaded runner → runs still 0).
    await asyncio.wait_for(started.wait(), timeout=0.5)
    # Second Start while the first onboarding is still in flight reuses it.
    await _runtime.start_neurocomment("listener-1")
    assert _runtime._ONBOARD_TASK is first_task
    assert runs == 1  # never ran concurrently; the second trigger only queued a rerun

    release.set()
    await _drain_onboarding()
    assert runs == 2  # the queued rerun executed after the first pass finished


@pytest.mark.asyncio
async def test_trigger_while_onboarding_in_flight_queues_one_rerun(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A trigger mid-run is not dropped: onboarding reruns once after the pass finishes."""
    await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    _patch_engine(monkeypatch, _ReconcileSpy())
    started = asyncio.Event()
    release = asyncio.Event()
    runs = 0

    async def slow_onboard(_campaign_id: str, **_kwargs: object) -> object:
        nonlocal runs
        runs += 1
        if runs == 1:
            started.set()
            await release.wait()
        return None

    monkeypatch.setattr(_runtime, "onboard_campaign", slow_onboard)

    await _runtime.start_neurocomment("listener-1")
    # Wait for the first pass to actually begin + block before triggering reruns
    # (sleep(0) races the onboarding task's to_thread DB read on a loaded runner).
    await asyncio.wait_for(started.wait(), timeout=0.5)
    # e.g. a channel link / account assignment lands while onboarding is running:
    _runtime._ensure_onboarding_running(None)
    _runtime._ensure_onboarding_running(None)  # coalesces with the first trigger
    release.set()
    await _drain_onboarding()

    assert runs == 2  # exactly one rerun, not one per trigger
    assert _runtime._ONBOARD_RERUN is False


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
    await _drain_onboarding()  # reconcile_if_running also spawned onboarding


@pytest.mark.asyncio
async def test_reconcile_if_running_spawns_onboarding_when_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A running-runtime reconcile also (re)triggers campaign onboarding (NOXX fix).

    A campaign whose channels/accounts were edited after Start would otherwise
    never get readiness rows until the operator pressed Start again.
    """
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    monkeypatch.setattr(_runtime, "reconcile_neurocomment_runtime", _ReconcileSpy().reconcile)
    onboarded: list[str] = []

    async def fake_onboard(campaign_id: str, **_kwargs: object) -> object:
        onboarded.append(campaign_id)
        return None

    monkeypatch.setattr(_runtime, "onboard_campaign", fake_onboard)

    # Paused: listener remembered, run flag off → no onboarding task.
    await set_listener_account_id("listener-1")
    await _runtime.reconcile_if_running()
    assert _runtime._ONBOARD_TASK is None

    # Running → onboarding spawned for the active campaign.
    await set_listener_running(running=True)
    await _runtime.reconcile_if_running()
    await _drain_onboarding()
    assert onboarded == [campaign.campaign_id]


@pytest.mark.asyncio
async def test_reconcile_on_startup_spawns_onboarding_when_resuming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A boot that resumes the listener also onboards campaigns created since the last Start."""
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    monkeypatch.setattr(_runtime, "reconcile_neurocomment_runtime", _ReconcileSpy().reconcile)
    onboarded: list[str] = []

    async def fake_onboard(campaign_id: str, **_kwargs: object) -> object:
        onboarded.append(campaign_id)
        return None

    monkeypatch.setattr(_runtime, "onboard_campaign", fake_onboard)
    await set_listener_account_id("listener-1")
    await set_listener_running(running=True)

    await _runtime.reconcile_neurocomment_on_startup()
    await _drain_onboarding()

    assert onboarded == [campaign.campaign_id]


@pytest.mark.asyncio
async def test_reconcile_on_startup_paused_does_not_spawn_onboarding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A paused listener stays paused on boot: no reconcile, and no onboarding either."""
    await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    monkeypatch.setattr(_runtime, "reconcile_neurocomment_runtime", _ReconcileSpy().reconcile)
    await set_listener_account_id("listener-1")  # remembered, run flag off

    await _runtime.reconcile_neurocomment_on_startup()

    assert _runtime._ONBOARD_TASK is None


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
    await _drain_onboarding()  # startup resume also spawned onboarding


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
async def test_startup_reclaims_stale_claims_even_when_not_running() -> None:
    """A crash-orphaned 'claimed' row is failed on boot even for a paused/stopped runtime.

    Reclaim runs before the running-gate, so a listener that boots not-running still
    frees claims stuck since before the cutoff (else the post_id is un-claimable forever).
    """
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, "@a")
    await create_account(AccountCreate(account_id="acc-1", label="acc-1", session_name="acc-1"))
    assert await claim_comment("@a", 1, campaign.campaign_id, "acc-1") is True
    # Stuck since well before the reclaim cutoff.
    stale = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    with _get_engine().begin() as connection:
        connection.exec_driver_sql(
            "UPDATE neurocomment_comments SET created_at = ? WHERE post_id = 1",
            (stale,),
        )
    assert await get_listener_running() is False  # runtime is not running

    await _runtime.reconcile_neurocomment_on_startup()

    row = await fetch_comment("@a", 1)
    assert row is not None
    assert row.status == "failed"


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
