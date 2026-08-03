"""Warming tests split from the former service test module: test_daily_limits.py."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime

import pytest

from core.config import settings
from core.db import (
    create_account,
    fetch_warming_state,
    save_warming_settings,
    upsert_warming_state,
)
from schemas.accounts import AccountCreate
from schemas.telegram_actions import ActionResult, TelegramAction
from schemas.warming import (
    ActivityPersona,
    StartWarmingRequest,
    StopWarmingRequest,
    WarmingCycleRequest,
    WarmingCycleResult,
    WarmingState,
    WarmingStateRecord,
    WarmingStateWrite,
)
from services import warming
from services.warming import _loop, _runtime, _seams
from services.warming._state import _set_state
from tests.services.warming._support import (
    _fake_loop,
    _Recorder,
    _seed_channel,
    _set_settings,
)


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


def _ok(account_id: str, action: TelegramAction) -> ActionResult:
    return ActionResult(
        status="ok",
        action_type=action.action_type,
        account_id=account_id,
        recent_message_ids=["101", "102"] if action.action_type == "read_channel" else None,
    )


class _CrashAfter:
    """Dispatcher that raises after N actions — models a cycle that blew up in-process."""

    def __init__(self, limit: int) -> None:
        self.actions: list[str] = []
        self._limit = limit

    async def execute(self, account_id: str, action: TelegramAction) -> ActionResult:
        if len(self.actions) >= self._limit:
            msg = "process killed"
            raise RuntimeError(msg)
        self.actions.append(action.action_type)
        return _ok(account_id, action)


class _CancelAfter(_CrashAfter):
    """Dispatcher that raises ``CancelledError`` after N actions.

    Byte-for-byte what ``task.cancel()`` does: the error surfaces at the innermost
    await, which is where every real cancellation of a warming cycle lands —
    lifespan shutdown, ``stop_warming``, and ``start_warming``'s cancel-and-replace.
    """

    async def execute(self, account_id: str, action: TelegramAction) -> ActionResult:
        if len(self.actions) >= self._limit:
            raise asyncio.CancelledError
        return await super().execute(account_id, action)


class _BlockOnce:
    """Dispatcher that parks forever on action N+1, then lets later ones through.

    Models a cycle caught mid-RPC. Actions after the park must be served normally:
    the cycle's ``SetOnline(False)`` cleanup runs while a cancellation unwinds, and
    a second park there would hang the cancel instead of letting it finish.
    """

    def __init__(self, limit: int) -> None:
        self.actions: list[str] = []
        self.reached = asyncio.Event()
        self._limit = limit
        self._parked = False

    async def execute(self, account_id: str, action: TelegramAction) -> ActionResult:
        if len(self.actions) >= self._limit and not self._parked:
            self._parked = True
            self.reached.set()
            await asyncio.Event().wait()
        self.actions.append(action.action_type)
        return _ok(account_id, action)


async def _seed_warming_account(run_id: str | None = None) -> None:
    """One account, one channel, readiness + chat off, parked in ``active``."""
    await _seed_channel()
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="acc-1", state="active", run_id=run_id),
    )


async def _iteration(run_id: str | None = None) -> None:
    """One loop iteration, returning None so it can live in ``_RUNTIME``."""
    await warming.run_loop_iteration("acc-1", run_id=run_id)


def _no_quiet_days(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "quiet_day_weekday_probability", 0.0)
    monkeypatch.setattr(settings.warming, "quiet_day_weekend_probability", 0.0)


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
    # The two actions really spent, not the whole reserved budget.
    assert record.daily_actions == 2

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
    assert record.daily_actions == 2


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
    assert record.daily_actions == 2

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
    assert record.daily_actions == 2


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

    # 10 already spent today + the 5 still available, booked up front.
    assert booked == [settings.warming.phase_daily_cap["intro"]]
    record = await fetch_warming_state("acc-1")
    assert record is not None
    # 10 already spent today + set_online + join + read + the story glance.
    assert record.daily_actions == 14


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
