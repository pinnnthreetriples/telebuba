"""Warming tests split from the former service test module: test_runtime_races.py."""

from __future__ import annotations

import asyncio

import pytest

from core.config import settings
from core.db import (
    create_account,
    fetch_warming_state,
    save_warming_settings,
    upsert_warming_state,
)
from schemas.accounts import AccountCreate
from schemas.warming import (
    StartWarmingRequest,
    WarmingCycleResult,
    WarmingStateWrite,
)
from services import warming
from services.warming import _loop, _runner, _runtime, _seams
from tests.services.warming._support import (
    _no_initial_delay,
    _Recorder,
    _seed_channel,
    _set_settings,
)


@pytest.mark.asyncio
async def test_stop_does_not_get_overwritten_by_inflight_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F1: a stop fired while ``run_one_cycle`` is in flight must stick."""
    from services.warming._loop import run_loop_iteration  # noqa: PLC0415

    await create_account(AccountCreate(account_id="acc-1"))
    await _seed_channel()
    # enforce_readiness off: this is a stop/CAS race test, not the П3 gate.
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="active"))

    # Patch ``run_one_cycle`` to simulate stop_warming firing mid-cycle:
    # the operator wrote ``idle`` while the loop was still inside this call.
    async def cycle_with_stop_inside(req, **_kwargs):  # type: ignore[no-untyped-def]
        await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="idle"))
        return WarmingCycleResult(account_id=req.account_id, status="ok")

    monkeypatch.setattr(_loop, "run_one_cycle", cycle_with_stop_inside)

    await run_loop_iteration("acc-1")
    state = await fetch_warming_state("acc-1")
    assert state is not None
    assert state.state == "idle"


@pytest.mark.asyncio
async def test_manual_start_replaces_existing_loop_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F2: re-starting an account must cancel a still-sleeping loop and create a fresh task."""
    started: list[str] = []
    cancelled = asyncio.Event()

    async def fake_loop(account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
        started.append(account_id)
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    monkeypatch.setattr(settings.warming, "enforce_readiness", False)
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )
    await create_account(AccountCreate(account_id="acc-1"))

    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))
    first_task = warming._RUNTIME["acc-1"]
    await asyncio.sleep(0)
    assert started == ["acc-1"]

    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))
    second_task = warming._RUNTIME["acc-1"]
    await asyncio.sleep(0)

    assert second_task is not first_task
    assert cancelled.is_set()
    assert started == ["acc-1", "acc-1"]


@pytest.mark.asyncio
async def test_old_cycle_cannot_overwrite_new_manual_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1.2: an in-flight cycle from a previous start must not write through.

    Simulates the race: cycle A is running ``run_one_cycle`` (state=active,
    run_id=A); meanwhile a second start_warming has flipped run_id → B and
    written 'queued' for the new generation. When A's iteration tries to write
    its final next_state, the run_id mismatch must turn the write into a no-op.
    """
    from services.warming._loop import run_loop_iteration  # noqa: PLC0415

    await create_account(AccountCreate(account_id="acc-1"))
    await _seed_channel()
    # enforce_readiness off: this is a run_id/CAS race test, not the П3 gate.
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)

    # Stage the DB: run_id_b is the "new" generation; the old cycle holds run_id_a.
    run_id_a = "old-run"
    run_id_b = "new-run"
    await upsert_warming_state(
        WarmingStateWrite(account_id="acc-1", state="active", run_id=run_id_a),
    )

    async def cycle_with_restart_inside(req, **_kwargs):  # type: ignore[no-untyped-def]
        # Simulate start_warming firing during this in-flight cycle: it minted
        # a fresh run_id and wrote it (along with state='active') to the row.
        await upsert_warming_state(
            WarmingStateWrite(
                account_id=req.account_id,
                state="active",
                run_id=run_id_b,
                last_event="queued",
            ),
        )
        return WarmingCycleResult(account_id=req.account_id, status="ok")

    monkeypatch.setattr(_loop, "run_one_cycle", cycle_with_restart_inside)

    await run_loop_iteration("acc-1", run_id=run_id_a)
    state = await fetch_warming_state("acc-1")
    assert state is not None
    # The new generation owns the row; the stale cycle's final write must not
    # have flipped state to 'sleeping'/'error' or rolled run_id back to A.
    assert state.run_id == run_id_b
    assert state.state == "active"
    assert state.last_event == "queued"


@pytest.mark.asyncio
async def test_remove_account_stops_runtime_task(monkeypatch: pytest.MonkeyPatch) -> None:
    """P3.7: removing an active warming account must stop its runtime task.

    Repo-level _delete_account is layer-correct in not touching _RUNTIME; the
    service-level ``remove_account`` is what callers should use to avoid leaving
    an orphan task that keeps trying to act on a vanished account.
    """
    from services.accounts.lifecycle import remove_account  # noqa: PLC0415

    started_events: list[str] = []
    cancelled_events: list[str] = []

    async def fake_loop(account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
        started_events.append(account_id)
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled_events.append(account_id)
            raise

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )
    await create_account(AccountCreate(account_id="acc-1"))

    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))
    await asyncio.sleep(0)
    assert "acc-1" in warming._RUNTIME

    await remove_account("acc-1")

    assert "acc-1" not in warming._RUNTIME
    assert cancelled_events == ["acc-1"]
    # DB row gone too.
    from core.db import fetch_account  # noqa: PLC0415

    assert await fetch_account("acc-1") is None


@pytest.mark.asyncio
async def test_real_stop_clears_run_id_so_stale_final_write_cannot_resurrect_idle() -> None:
    """Round-4 P1.1: drives the *real* _stop_warming_locked + a stale CAS write.

    Earlier round 3 test simulated stop with a hand-written upsert that
    cleared run_id — that masked a live bug where _stop_warming_locked did
    NOT clear run_id, so a stale loop's CAS write (run_id still matches)
    could sneak past and overwrite ``idle`` with ``sleeping``. This test
    invokes the real stop helper and asserts both legs of the fix:
    (1) stop clears run_id, (2) even if it did not, the upsert's CAS
    rejects any UPDATE that would overwrite an idle row.
    """
    from services.warming._runtime import _stop_warming_locked  # noqa: PLC0415
    from services.warming._state import _set_state  # noqa: PLC0415

    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="acc-1", state="active", run_id="run-a"),
    )

    # Real stop. Must clear run_id (belt) so any stale CAS using run-a misses.
    await _stop_warming_locked("acc-1")
    state = await fetch_warming_state("acc-1")
    assert state is not None
    assert state.state == "idle"
    assert state.run_id is None

    # Now manually re-stamp run_id to simulate a future regression where
    # stop forgot to clear it. The CAS-rejects-idle suspenders must still
    # protect the row from a stale loop's write.
    await upsert_warming_state(
        WarmingStateWrite(account_id="acc-1", state="idle", run_id="run-a"),
    )
    await _set_state(
        "acc-1",
        "sleeping",
        last_event="cycle:ok",
        expected_run_id="run-a",
    )
    state = await fetch_warming_state("acc-1")
    assert state is not None
    assert state.state == "idle"  # suspenders held — the stale write was a no-op
    assert state.last_event != "cycle:ok"


@pytest.mark.asyncio
async def test_restart_between_run_id_check_and_cycle_started_write_loses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round-2 P1: a stale iteration cannot stamp 'cycle_started' on top of a new run.

    Forces the race by patching ``fetch_warming_state`` so the first call (the
    iteration's _matches_active_run guard) sees the OLD run_id, but the row in
    the DB has already been advanced to a fresh run_id by a new start_warming.
    The CAS clause on the cycle_started upsert must then refuse to mutate the
    row — the new generation's state must survive untouched.
    """
    from services.warming._loop import run_loop_iteration  # noqa: PLC0415

    await create_account(AccountCreate(account_id="acc-1"))
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")
    # DB row is on the NEW generation already.
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="active",
            last_event="queued",
            run_id="run-b",
        ),
    )

    # The stale guard sees a stale snapshot (run_id=run-a). The CAS on the
    # subsequent _set_state must catch the mismatch and skip the UPDATE.
    real_fetch = _loop.fetch_warming_state

    fetch_calls = {"n": 0}

    async def fake_fetch(account_id: str):  # type: ignore[no-untyped-def]
        fetch_calls["n"] += 1
        if fetch_calls["n"] == 1:
            # First fetch is the guard; lie about run_id so the guard accepts.
            real = await real_fetch(account_id)
            if real is None:
                return real
            return real.model_copy(update={"run_id": "run-a"})
        return await real_fetch(account_id)

    monkeypatch.setattr(_loop, "fetch_warming_state", fake_fetch)

    # Stub the cycle so we don't reach real Telethon — the CAS we're testing
    # fires on cycle_started *before* the cycle runs, so the stub's content
    # doesn't matter for the assertion.
    async def stub_cycle(req):  # type: ignore[no-untyped-def]
        return WarmingCycleResult(account_id=req.account_id, status="ok")

    monkeypatch.setattr(_loop, "run_one_cycle", stub_cycle)

    await run_loop_iteration("acc-1", run_id="run-a")
    state = await fetch_warming_state("acc-1")
    assert state is not None
    # The stale cycle_started write was a no-op; new generation's row stands.
    assert state.run_id == "run-b"
    assert state.last_event == "queued"


@pytest.mark.asyncio
async def test_run_loop_iteration_bails_when_state_error() -> None:
    """Round-2 P2.3: direct call on error account must not resurrect a cycle."""
    from services.warming._loop import run_loop_iteration  # noqa: PLC0415

    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="acc-1", state="error", last_error="boom"),
    )

    result = await run_loop_iteration("acc-1")
    assert result.status == "skipped"
    state = await fetch_warming_state("acc-1")
    assert state is not None
    assert state.state == "error"
    assert state.last_error == "boom"


@pytest.mark.asyncio
async def test_remove_account_blocks_concurrent_start_until_delete_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round-2 P2.2: remove_account holds the lifecycle lock across stop + delete.

    Forces the race shape: a parallel ``start_warming`` is dispatched while
    ``remove_account`` is mid-flight. With the lock held across both stop and
    delete, the start has to wait until delete finishes; by then the account
    is gone, so start raises UnknownAccountError and no orphan task is
    created. Without the lock, the start would interleave and produce an
    orphan task pointing at a deleted account.
    """
    from services.accounts.lifecycle import remove_account  # noqa: PLC0415

    started_events: list[str] = []

    async def fake_loop(account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
        started_events.append(account_id)
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )
    await create_account(AccountCreate(account_id="acc-1"))
    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))
    await asyncio.sleep(0)
    started_events.clear()  # drop the legitimate first start

    # Run remove and a concurrent start. If the lock isn't held, the start
    # races into _RUNTIME before delete_account; if it is, start waits for
    # the lock, finds the account gone, and bails with UnknownAccountError.
    remove_task = asyncio.create_task(remove_account("acc-1"))
    await asyncio.sleep(0)  # give remove a chance to take the lock

    with pytest.raises(warming.UnknownAccountError):
        await warming.start_warming(StartWarmingRequest(account_id="acc-1"))

    await remove_task

    # No orphan task survived the race.
    assert "acc-1" not in warming._RUNTIME
    assert started_events == []


@pytest.mark.asyncio
async def test_stale_cycle_started_cas_failure_prevents_telegram_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round-4 P1.2: a CAS no-op on cycle_started must abort run_one_cycle.

    Forces the race: the iteration's initial _matches_active_run guard accepts
    the stale run_id (we lie via fetch_warming_state), but the row in the DB
    is on a newer run_id, so the cycle_started upsert's CAS WHERE clause
    matches no rows (rowcount=0 → applied=False). The iteration must turn
    that into ``status='skipped'`` and never reach run_one_cycle. Otherwise
    the stale loop would happily issue Telegram actions (join / read / DM)
    on behalf of a generation that's been replaced.
    """
    from services.warming._loop import run_loop_iteration  # noqa: PLC0415

    await create_account(AccountCreate(account_id="acc-1"))
    await _seed_channel()
    # enforce_readiness off: this is a stale-cycle CAS test, not the П3 gate.
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)
    # DB row carries the NEW generation already.
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="active",
            last_event="queued",
            run_id="run-b",
        ),
    )

    # Lie to the iteration's guard so it proceeds; the CAS underneath will
    # still see run-b and reject the stale UPDATE.
    real_fetch = _loop.fetch_warming_state
    fetch_calls = {"n": 0}

    async def fake_fetch(account_id: str):  # type: ignore[no-untyped-def]
        fetch_calls["n"] += 1
        if fetch_calls["n"] == 1:
            real = await real_fetch(account_id)
            if real is None:
                return real
            return real.model_copy(update={"run_id": "run-a"})
        return await real_fetch(account_id)

    monkeypatch.setattr(_loop, "fetch_warming_state", fake_fetch)

    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)

    result = await run_loop_iteration("acc-1", run_id="run-a")
    assert result.status == "skipped"
    assert result.detail == "stale run"
    # The point of the fix: NO Telegram actions on behalf of the stale loop.
    assert recorder.actions == []


@pytest.mark.asyncio
async def test_start_warming_and_start_neurocomment_are_mutually_exclusive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RACE 1: an account can't run as warming AND the NC listener at once.

    ``start_neurocomment`` now takes the same per-account lifecycle lock
    ``start_warming`` holds, wrapping its warming-check → listener-commit. Racing
    the two for one account, exactly one wins: if warming commits first the NC
    start sees the account warming and raises ``ListenerBusyWarmingError``; if the
    listener commits first, ``start_warming`` sees the account is the running
    listener and raises ``AccountIsListenerError``. Before the fix both could
    commit (the NC check and commit were separated by awaits), leaving the account
    claimed by both runtimes.
    """
    from contextlib import suppress  # noqa: PLC0415

    from core.db import get_listener_running, list_warming_account_ids  # noqa: PLC0415
    from services.neurocomment import _runtime as nc_runtime  # noqa: PLC0415

    async def fake_loop(account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)

    # The listener commit is what races warming; stub start_neurocomment's
    # post-commit network/task work so the test exercises only the guarded path.
    async def _noop_reconcile(_listener: str) -> None:
        return None

    monkeypatch.setattr(nc_runtime, "reconcile_neurocomment_runtime", _noop_reconcile)
    monkeypatch.setattr(nc_runtime, "_ensure_onboarding_running", lambda *a, **k: None)  # noqa: ARG005
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )
    await create_account(AccountCreate(account_id="acc-1"))

    results = await asyncio.gather(
        warming.start_warming(StartWarmingRequest(account_id="acc-1")),
        nc_runtime.start_neurocomment("acc-1"),
        return_exceptions=True,
    )

    errors = [r for r in results if isinstance(r, Exception)]
    successes = [r for r in results if not isinstance(r, Exception)]
    assert len(errors) == 1, results
    assert len(successes) == 1, results
    assert isinstance(
        errors[0],
        (warming.AccountIsListenerError, nc_runtime.ListenerBusyWarmingError),
    )

    # Exactly one runtime claims the account — never both.
    is_warming_now = "acc-1" in await list_warming_account_ids()
    listener_running = await get_listener_running()
    assert is_warming_now != listener_running

    # Hygiene: cancel any warming loop task the winning start spawned.
    task = warming._RUNTIME.pop("acc-1", None)
    if task is not None:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_start_neurocomment_stop_race_leaves_no_orphan_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RACE 2: a stop racing start's subscribe must not leave a live listener after pause.

    Pre-fix, ``start_neurocomment`` set ``listener_running=True`` under the lifecycle
    lock, RELEASED it, then subscribed outside the lock. A ``stop_neurocomment`` could
    slot in after the release: it shut down (nothing subscribed yet → no-op) and cleared
    the flag, and then start's late ``subscribe_posts`` created a LIVE listener while the
    persisted flag said paused — an orphan that kept posting after the operator paused.

    The fix holds the lock across reconcile/subscribe, so stop serializes AFTER the
    subscription and its shutdown tears the listener down. We drive the exact window:
    start is parked inside ``subscribe_posts``; whether stop can complete meanwhile is
    precisely what the lock scope decides. Invariant asserted: if the persisted flag is
    False, NO live subscription remains. The listener I/O is a real recorder (not a
    no-op stub) so the orphan is observable — stubbing reconcile would mask this bug.
    """
    from core.db import (  # noqa: PLC0415
        create_campaign,
        get_listener_running,
        link_channel_to_campaign,
    )
    from schemas.neurocomment import CampaignCreate  # noqa: PLC0415
    from services.neurocomment import _runtime as nc_runtime  # noqa: PLC0415
    from tests.services.neurocomment.runtime_support import (  # noqa: PLC0415
        _ExecuteSpy,
        _patch_execute,
    )

    nc_runtime.reset_for_tests()  # this file's conftest resets warming, not NC state
    monkeypatch.setattr(settings.neurocomment, "deletion_sweep_interval_seconds", 0)
    _patch_execute(monkeypatch, _ExecuteSpy())  # reconcile's JoinChannel seam → ok
    monkeypatch.setattr(nc_runtime, "_ensure_onboarding_running", lambda *a, **k: None)  # noqa: ARG005

    # One active watch channel so reconcile reaches subscribe (empty set → it stops+returns).
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, "@a")

    # Real recorder: subscribe marks the listener live, stop clears it. Parked inside
    # subscribe until released, so stop only proceeds if the lock is not held across it.
    live = {"subscribed": False}
    at_subscribe = asyncio.Event()
    release = asyncio.Event()

    async def fake_subscribe(account_id, channels, on_post):  # type: ignore[no-untyped-def]  # noqa: ARG001
        at_subscribe.set()
        await release.wait()
        live["subscribed"] = True

    async def fake_stop(account_id: str) -> None:  # noqa: ARG001
        live["subscribed"] = False

    monkeypatch.setattr(nc_runtime, "subscribe_posts", fake_subscribe)
    monkeypatch.setattr(nc_runtime, "stop_post_listener", fake_stop)

    start_task = asyncio.create_task(nc_runtime.start_neurocomment("acc-1"))
    await asyncio.wait_for(at_subscribe.wait(), timeout=1.0)  # start is parked in subscribe
    stop_task = asyncio.create_task(nc_runtime.stop_neurocomment())
    # Give stop every chance to acquire the lock and finish: it can only do so while
    # start is parked in subscribe if start already RELEASED the lock (the pre-fix
    # bug). Bounded so the fixed path — stop blocked on the still-held lock — simply
    # waits out the budget with the flag still True, then we release.
    for _ in range(50):
        if await get_listener_running() is False:
            break
        await asyncio.sleep(0.001)
    release.set()  # subscribe completes → live=True
    await asyncio.gather(start_task, stop_task)

    # The invariant: paused persisted state ⇒ no live listener survives.
    assert await get_listener_running() is False
    assert live["subscribed"] is False


@pytest.mark.asyncio
async def test_stale_loop_crash_cannot_overwrite_new_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round-6 P1: a crashing stale loop must not stamp 'error' on the new run.

    Without the generation check + CAS in the crash handler, the loop's
    ``except Exception`` branch wrote ``error`` via _set_state without an
    ``expected_run_id``, so a stale generation that fell over after the
    operator restarted the account would overwrite the new generation's
    row with state=error and a misleading ``last_event='loop_crashed'``.
    """
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="acc-1", state="active", run_id="run-a"),
    )

    monkeypatch.setattr(_runner, "_initial_delay_seconds", _no_initial_delay)
    monkeypatch.setattr(_runner, "_loop_sleep_seconds", lambda *_args, **_kwargs: 0.0)

    async def crash_after_replacing_generation(
        account_id: str, *, run_id: str | None = None
    ) -> WarmingCycleResult:
        # A new start_warming raced this iteration: row now carries run-b.
        await upsert_warming_state(
            WarmingStateWrite(account_id=account_id, state="active", run_id="run-b"),
        )
        del run_id  # we are the stale loop; bury our own marker
        msg = "boom from stale loop"
        raise RuntimeError(msg)

    monkeypatch.setattr(_runner, "run_loop_iteration", crash_after_replacing_generation)

    await _runner._warming_loop("acc-1", run_id="run-a")

    state = await fetch_warming_state("acc-1")
    assert state is not None
    # The new generation's row survives — neither state nor run_id was touched.
    assert state.state == "active"
    assert state.run_id == "run-b"
    assert state.last_event != "loop_crashed"
    assert state.last_error is None
