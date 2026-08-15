"""Warming tests split from the former service test module: test_daily_limits.py."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING

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
    ActivityPersona,
    StartWarmingRequest,
    StopWarmingRequest,
    WarmingCycleRequest,
    WarmingCycleResult,
    WarmingHandBack,
    WarmingState,
    WarmingStateRecord,
    WarmingStateWrite,
)
from services import warming
from services.warming import _loop, _reservation, _runtime, _seams, _steps
from services.warming._state import _set_state
from tests.services.warming._support import (
    _BlockOnce,
    _CancelAfter,
    _CrashAfter,
    _fake_loop,
    _iteration,
    _no_quiet_days,
    _Recorder,
    _seed_channel,
    _seed_warming_account,
    _set_settings,
)

if TYPE_CHECKING:
    from schemas.telegram_actions import ActionResult, TelegramAction


def test_roll_daily_resets_on_new_day() -> None:
    record = WarmingStateRecord(
        account_id="a",
        state="sleeping",
        updated_at="t",
        daily_actions=5,
        daily_count_date="2026-06-11",
    )
    assert warming._roll_daily(record, "2026-06-12") == (0, "2026-06-12")


def test_roll_daily_keeps_same_day() -> None:
    record = WarmingStateRecord(
        account_id="a",
        state="sleeping",
        updated_at="t",
        daily_actions=5,
        daily_count_date="2026-06-12",
    )
    assert warming._roll_daily(record, "2026-06-12") == (5, "2026-06-12")


def test_roll_daily_handles_missing_record() -> None:
    assert warming._roll_daily(None, "2026-06-12") == (0, "2026-06-12")


@pytest.mark.asyncio
async def test_run_loop_iteration_parks_when_daily_cap_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_quiet_days(monkeypatch)
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    # A fresh account is intro-capped by the auto cap (П2 retired the fleet-wide
    # override); enforce_readiness off so the daily gate is the one that fires,
    # not the П3 readiness gate.
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )
    await create_account(AccountCreate(account_id="acc-1"))
    today = datetime.now(UTC).date().isoformat()
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="sleeping",
            daily_actions=settings.warming.phase_daily_cap["intro"],
            daily_count_date=today,
        ),
    )

    result = await warming.run_loop_iteration("acc-1")

    assert result.status == "skipped"
    assert result.detail == "daily limit"
    assert recorder.actions == []
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.state == "sleeping"
    assert record.next_run_at is not None


@pytest.mark.asyncio
async def test_phase_cap_governs_daily_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The per-account auto cap (phase/trust) is the sole daily governor (audit П2;
    # the legacy fleet-wide override was removed). A fresh account is intro-capped
    # at the intro cap, so a daily_actions already at that cap parks the account.
    # enforce_readiness off so the daily gate is reached, not the П3 readiness gate.
    _no_quiet_days(monkeypatch)
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )
    await create_account(AccountCreate(account_id="acc-1"))
    today = datetime.now(UTC).date().isoformat()
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="sleeping",
            daily_actions=settings.warming.phase_daily_cap["intro"],
            daily_count_date=today,
        ),
    )

    result = await warming.run_loop_iteration("acc-1")

    assert result.status == "skipped"
    assert result.detail == "daily limit"
    assert recorder.actions == []


@pytest.mark.asyncio
async def test_run_loop_iteration_increments_daily_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_quiet_days(monkeypatch)
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")
    await create_account(AccountCreate(account_id="acc-1"))

    await warming.run_loop_iteration("acc-1")

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.daily_count_date == datetime.now(UTC).date().isoformat()
    # One channel per cycle: set_online + join + read + the story glance = 4
    # attempts (set_offline does not count). The story step is last before the DM
    # step, so it only fits now that the intro cap leaves room past the reads — at
    # the old cap of 3 the cycle was truncated right after the read.
    assert record.daily_actions == 4


@pytest.mark.asyncio
async def test_daily_limit_excludes_offline_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")

    # Give it only 2 remaining actions: SetOnline(True) uses 1, Join uses 1.
    # It should not attempt Read, but SetOnline(False) should still run.
    result = await warming.run_one_cycle(
        WarmingCycleRequest(account_id="acc-1", remaining_actions=2)
    )

    assert result.attempted_actions == 2
    types = recorder.types()
    assert types == ["set_online", "join_channel", "set_online"]
    assert result.channels_joined == 1
    assert result.channels_read == 0


@pytest.mark.asyncio
async def test_daily_budget_is_not_respent_after_a_hard_kill_mid_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#208: a vanished process must not hand the restarted loop a fresh budget.

    A SIGKILL runs no handler at all, so the only thing that can protect the cap is
    what the row already holds — the pre-cycle reservation. Simulated by abandoning
    a cycle task parked mid-RPC: nothing in-process ever unwinds, exactly as if the
    interpreter had gone away.
    """
    _no_quiet_days(monkeypatch)
    killed = _BlockOnce(2)
    monkeypatch.setattr(_seams, "execute", killed.execute)
    await _seed_warming_account()
    task = asyncio.create_task(_iteration())
    await asyncio.wait_for(killed.reached.wait(), timeout=5)

    # The process is gone after SetOnline + the join, before ``_finalize_after_cycle``.
    assert killed.actions == ["set_online", "join_channel"]
    record = await fetch_warming_state("acc-1")
    assert record is not None
    # The row still carries the pre-cycle reservation, so today's budget is spent.
    assert record.daily_actions == settings.warming.phase_daily_cap["intro"]

    # Restart: the reconciled loop re-runs the iteration on the same calendar day.
    survivor = _Recorder()
    monkeypatch.setattr(_seams, "execute", survivor.execute)

    result = await warming.run_loop_iteration("acc-1")

    assert result.detail == "daily limit"
    assert survivor.actions == []

    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_a_cancelled_cycle_hands_back_the_unspent_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A graceful cancel (deploy/shutdown) must cost only what the cycle spent (#208)."""
    _no_quiet_days(monkeypatch)
    cancelled = _CancelAfter(2)
    monkeypatch.setattr(_seams, "execute", cancelled.execute)
    await _seed_warming_account()

    with pytest.raises(asyncio.CancelledError):
        await warming.run_loop_iteration("acc-1")

    assert cancelled.actions == ["set_online", "join_channel"]
    record = await fetch_warming_state("acc-1")
    assert record is not None
    # The third call crossed the gateway boundary before cancellation surfaced,
    # so its outcome is ambiguous and is conservatively counted too.
    assert record.daily_actions == 3

    # Same calendar day, restarted loop: the rest of the budget is still there.
    survivor = _Recorder()
    monkeypatch.setattr(_seams, "execute", survivor.execute)

    result = await warming.run_loop_iteration("acc-1")

    assert result.detail != "daily limit"
    assert survivor.types()[0] == "set_online"


@pytest.mark.asyncio
async def test_a_raising_cycle_hands_back_the_unspent_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An in-process crash still reconciles: the loop wrapper parks the account, not the day."""
    _no_quiet_days(monkeypatch)
    crash = _CrashAfter(2)
    monkeypatch.setattr(_seams, "execute", crash.execute)
    await _seed_warming_account()

    with pytest.raises(RuntimeError):
        await warming.run_loop_iteration("acc-1")

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.daily_actions == 3


@pytest.mark.asyncio
async def test_finalize_hands_back_the_reservation_when_the_row_left_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A finalize that finds the row parked elsewhere must still release the reservation.

    No cancellation is involved here: the cycle finishes normally, but the row was
    moved to ``error`` behind it, so ``_finalize_after_cycle`` early-returns before
    its reconciling write.
    """
    _no_quiet_days(monkeypatch)
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    calculate_next_run = _loop._calculate_next_run

    async def park_then_calculate(
        account_id: str,
        result: WarmingCycleResult,
        persona: ActivityPersona,
        daily_cap: int,
    ) -> tuple[int, datetime, WarmingState]:
        # Fires between the last action and the finalize write, like a readiness
        # park (or a stop that outran its cancel timeout) landing behind the cycle.
        await _set_state("acc-1", "error", expected_run_id="run-1")
        return await calculate_next_run(account_id, result, persona, daily_cap)

    monkeypatch.setattr(_loop, "_calculate_next_run", park_then_calculate)
    await _seed_warming_account(run_id="run-1")

    await warming.run_loop_iteration("acc-1", run_id="run-1")

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.state == "error"
    # set_online + join + read + the story glance, not the 15-action reservation.
    assert record.daily_actions == 4


@pytest.mark.asyncio
async def test_stop_then_start_on_the_same_day_keeps_the_remaining_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One Stop click and one Start click must not forfeit the rest of the day (#208)."""
    _no_quiet_days(monkeypatch)
    monkeypatch.setattr(_runtime, "_warming_loop", _fake_loop)
    blocked = _BlockOnce(2)
    monkeypatch.setattr(_seams, "execute", blocked.execute)
    await _seed_warming_account(run_id="run-1")
    warming._RUNTIME["acc-1"] = asyncio.create_task(_iteration("run-1"))
    await asyncio.wait_for(blocked.reached.wait(), timeout=5)

    stopped = await warming.stop_warming(StopWarmingRequest(account_id="acc-1"))

    assert stopped.state == "idle"
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.daily_actions == 3

    survivor = _Recorder()
    monkeypatch.setattr(_seams, "execute", survivor.execute)
    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))
    restarted = await fetch_warming_state("acc-1")
    assert restarted is not None

    result = await warming.run_loop_iteration("acc-1", run_id=restarted.run_id)

    assert result.detail != "daily limit"
    assert survivor.types()[0] == "set_online"


@pytest.mark.asyncio
async def test_restart_while_a_cycle_is_in_flight_keeps_the_remaining_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``start_warming`` cancels the old cycle BEFORE stamping the new generation (#208).

    The dying cycle's reconcile is CAS-guarded on the old ``run_id``, so a cancel
    ordered after the new marker is written would have the write refused and the
    fresh stint would inherit the whole reserved budget.
    """
    _no_quiet_days(monkeypatch)
    monkeypatch.setattr(_runtime, "_warming_loop", _fake_loop)
    blocked = _BlockOnce(2)
    monkeypatch.setattr(_seams, "execute", blocked.execute)
    await _seed_warming_account(run_id="run-1")
    warming._RUNTIME["acc-1"] = asyncio.create_task(_iteration("run-1"))
    await asyncio.wait_for(blocked.reached.wait(), timeout=5)

    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.run_id != "run-1"
    assert record.daily_actions == 3


@pytest.mark.asyncio
async def test_daily_counter_accumulates_on_top_of_a_mid_day_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both halves of the reserve/reconcile arithmetic must hold from a non-zero count."""
    _no_quiet_days(monkeypatch)
    recorder = _Recorder()
    booked: list[int] = []

    async def snapshot_then_execute(account_id: str, action: TelegramAction) -> ActionResult:
        if not booked:
            # First action of the cycle: the reservation is on the row by now.
            row = await fetch_warming_state(account_id)
            booked.append(row.daily_actions if row is not None else -1)
        return await recorder.execute(account_id, action)

    monkeypatch.setattr(_seams, "execute", snapshot_then_execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)
    await create_account(AccountCreate(account_id="acc-1"))
    today = datetime.now(UTC).date().isoformat()
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="active",
            daily_actions=10,
            daily_count_date=today,
        ),
    )

    await warming.run_loop_iteration("acc-1")

    # 10 already spent today + the 5 still available, booked up front. Spelled out
    # rather than as the cap: the daily gate guarantees count <= cap, so comparing
    # against the cap cannot tell ``daily_count + remaining`` from a bare cap.
    assert booked == [10 + 5]
    record = await fetch_warming_state("acc-1")
    assert record is not None
    # 10 already spent today + set_online + join + read + the story glance.
    assert record.daily_actions == 14


@pytest.mark.asyncio
async def test_a_cancelled_cycle_reconciles_on_top_of_a_mid_day_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reconcile ADDS to today's count — it must not overwrite it (#208).

    Every other reconcile test starts the day at zero, where the left operand of
    ``daily_count + spent`` is invisible. From 10 spent under a cap of 15, writing
    a bare ``spent`` would hand the restarted loop 13 more actions.
    """
    _no_quiet_days(monkeypatch)
    cancelled = _CancelAfter(2)
    monkeypatch.setattr(_seams, "execute", cancelled.execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="active",
            daily_actions=10,
            daily_count_date=datetime.now(UTC).date().isoformat(),
        ),
    )

    with pytest.raises(asyncio.CancelledError):
        await warming.run_loop_iteration("acc-1")

    assert cancelled.actions == ["set_online", "join_channel"]
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.daily_actions == 10 + 3


@pytest.mark.asyncio
async def test_a_cancel_in_the_reading_pause_counts_the_read_it_already_spent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reconcile must count requests that left the process, not folds (#208).

    The post-read pause is the longest await in a cycle (8-45s), so it is where a
    deploy's cancel most often lands — and the read RPC is already spent by then.
    Counting it only when the channel walk folds the outcome in, after the pause,
    under-counts: the restarted loop would then re-spend that action on top.
    """
    _no_quiet_days(monkeypatch)
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)

    async def cancel_in_the_reading_pause(_min_seconds: float, _max_seconds: float) -> None:
        if recorder.types()[-1:] == ["read_channel"]:
            raise asyncio.CancelledError

    monkeypatch.setattr(_steps, "_human_pause", cancel_in_the_reading_pause)
    await _seed_warming_account()

    with pytest.raises(asyncio.CancelledError):
        await warming.run_loop_iteration("acc-1")

    # set_online + join + the read really left the process; the trailing entry is
    # the offline cleanup, which never counts against the cap.
    assert recorder.types() == ["set_online", "join_channel", "read_channel", "set_online"]
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.daily_actions == 3


@pytest.mark.asyncio
async def test_the_uncancelled_cycle_counts_every_action_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Counting at the spend site must not double-count on the normal path.

    Reactions on, so both of the read/react step's requests are exercised: if the
    per-request increments and a fold-on-return ever coexist, the daily counter
    doubles and the account parks at half its cap.
    """
    _no_quiet_days(monkeypatch)
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=True, key="", enforce_readiness=False)
    await create_account(AccountCreate(account_id="acc-1"))

    await warming.run_loop_iteration("acc-1")

    assert recorder.types() == [
        "set_online",
        "join_channel",
        "read_channel",
        "react_to_post",
        "watch_peer_stories",
        "set_online",
    ]
    record = await fetch_warming_state("acc-1")
    assert record is not None
    # The five billable requests above, each counted once (the offline cleanup is
    # excluded by design).
    assert record.daily_actions == 5


@pytest.mark.asyncio
async def test_a_second_cancel_during_the_reconcile_keeps_the_original_exception(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``shutdown_warming_runtime`` cancels a second time when its 5s gather times out.

    That cancel lands on the reconcile's own write. Unshielded it would abandon
    the write (the whole reservation stays booked, silently) and replace the
    exception being propagated — a genuine crash relabelled ``CancelledError``
    skips ``_runner``'s crash branch, so nothing is logged and the account is
    never parked in ``error``.
    """
    _no_quiet_days(monkeypatch)
    crash = _CrashAfter(2)
    monkeypatch.setattr(_seams, "execute", crash.execute)
    real_hand_back = _reservation.hand_back_warming_reservation
    written = asyncio.Event()
    task: asyncio.Task[None]

    async def cancel_then_write(
        account_id: str, *, token: str, booked: int, reconciled: int, daily_date: str
    ) -> WarmingHandBack:
        # The reconcile is this write's only caller, so every call is the one under
        # test — no need to fingerprint it by its values.
        task.cancel()
        await asyncio.sleep(0)  # delivered mid-write, exactly as to_thread would
        outcome = await real_hand_back(
            account_id, token=token, booked=booked, reconciled=reconciled, daily_date=daily_date
        )
        written.set()
        return outcome

    monkeypatch.setattr(_reservation, "hand_back_warming_reservation", cancel_then_write)
    await _seed_warming_account()
    task = asyncio.create_task(_iteration())

    with (
        caplog.at_level(logging.WARNING, logger="services.warming._reservation"),
        pytest.raises(RuntimeError),
    ):
        await task

    # The write finished despite the second cancel, and the cancel was not silent —
    # logged as "interrupted", not "failed": the shielded write did land.
    await asyncio.wait_for(written.wait(), timeout=5)
    assert "reservation reconcile interrupted" in caplog.text
    assert "reservation reconcile failed" not in caplog.text
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.daily_actions == 3


@pytest.mark.asyncio
async def test_a_raising_finalize_still_hands_back_the_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A finalize that raises must not cost the rest of the day as well (#208).

    ``_finalize_after_cycle`` is three DB round-trips; this module already
    anticipates a transient SQLite lock on them. Outside the handler's reach, such
    a lock left the full reservation booked, so the operator's next Start parked
    the account on a phantom "daily limit" on top of the ``error`` state.
    """
    _no_quiet_days(monkeypatch)
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)

    async def park_then_raise(*_args: object, **_kwargs: object) -> WarmingCycleResult:
        # A readiness park moved the row behind the cycle, then the finalize write
        # trips on a lock — the reconcile must echo the row's state, not "active".
        await _set_state("acc-1", "error", expected_run_id="run-1")
        msg = "database is locked"
        raise RuntimeError(msg)

    monkeypatch.setattr(_loop, "_finalize_after_cycle", park_then_raise)
    await _seed_warming_account(run_id="run-1")

    with pytest.raises(RuntimeError):
        await warming.run_loop_iteration("acc-1", run_id="run-1")

    record = await fetch_warming_state("acc-1")
    assert record is not None
    # Echoed back, not resurrected as ``active``.
    assert record.state == "error"
    # set_online + join + read + the story glance, not the 15-action reservation.
    assert record.daily_actions == 4


@pytest.mark.parametrize("boom", [RuntimeError("handler blew up"), asyncio.CancelledError()])
@pytest.mark.asyncio
async def test_a_failing_reconcile_never_replaces_the_cycles_own_exception(
    monkeypatch: pytest.MonkeyPatch,
    boom: BaseException,
) -> None:
    """The hand-back runs on the way out of another exception and must not mask it.

    ``_runner`` parks the account in ``error`` from the cycle's own exception; a
    handler that let its own failure escape would deliver that crash relabelled,
    and as a ``CancelledError`` it would skip the crash branch altogether.
    """
    _no_quiet_days(monkeypatch)
    crash = _CrashAfter(2)
    monkeypatch.setattr(_seams, "execute", crash.execute)
    real_fetch = _reservation.fetch_warming_state

    async def fetch_then_raise(account_id: str) -> WarmingStateRecord | None:
        # Only the handler's read: the pre-cycle one runs before any action lands,
        # and the crash means the finalize's read is never reached.
        if crash.actions:
            raise boom
        return await real_fetch(account_id)

    monkeypatch.setattr(_reservation, "fetch_warming_state", fetch_then_raise)
    await _seed_warming_account()

    # No hand-back is possible, so the day is forfeited — but the cycle's own
    # ``RuntimeError`` is what ``_runner`` sees, not the handler's.
    with pytest.raises(RuntimeError, match="process killed"):
        await warming.run_loop_iteration("acc-1")


@pytest.mark.asyncio
async def test_daily_gate_allows_one_cycle_for_a_cap_of_one() -> None:
    """A tiny cap (e.g. a legacy override of 1) must still run once a day, not park forever."""
    await create_account(AccountCreate(account_id="acc-1"))
    today = datetime.now(UTC).date().isoformat()
    now = datetime.now(UTC)

    # cap=1, nothing done yet -> the gate lets the cycle proceed (returns None).
    assert await _loop._gate_daily_limit("acc-1", 1, (0, today), now, run_id=None) is None
    # cap=1, the one action already spent today -> park.
    parked = await _loop._gate_daily_limit("acc-1", 1, (1, today), now, run_id=None)
    assert parked is not None
    assert parked.detail == "daily limit"
