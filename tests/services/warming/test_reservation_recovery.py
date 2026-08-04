"""Reservation hand-back recovery — the stop path that outran its cancel wait (#10).

``_reservation`` books the whole remaining daily budget before a cycle runs and
reconciles it down on the way out (#208). These tests cover the two directions that
hand-back has to hold in at once: a stop/restart must not forfeit the rest of the
day, and no hand-back may lower a count it did not book.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (
    create_account,
    fetch_warming_state,
    hand_back_warming_reservation,
    upsert_warming_state,
)
from schemas.accounts import AccountCreate
from schemas.warming import (
    StartWarmingRequest,
    StopWarmingRequest,
    WarmingCycleResult,
    WarmingState,
    WarmingStateWrite,
)
from services import warming
from services.warming import _loop, _reservation, _runtime, _seams
from services.warming._reservation import _Reservation
from tests.services.warming._support import (
    _fake_loop,
    _iteration,
    _no_quiet_days,
    _ok,
    _Recorder,
    _seed_warming_account,
)

if TYPE_CHECKING:
    from schemas.telegram_actions import ActionResult, TelegramAction


class _StallOnTheWayOut:
    """Parks mid-cycle, then stalls the ``SetOnline(False)`` cleanup while unwinding.

    The shape of the defect: the cancel lands on an RPC, and the cycle's ``finally``
    cleanup then talks to a slow proxy for longer than ``stop_cancel_timeout_seconds``.
    ``stop_warming`` gives up waiting and writes ``idle`` with the generation cleared,
    so the reservation hand-back that follows the cleanup arrives at a row that no
    live run owns.
    """

    def __init__(self, limit: int) -> None:
        self.actions: list[str] = []
        self.reached = asyncio.Event()
        self.unwinding = asyncio.Event()
        self.release = asyncio.Event()
        self._limit = limit
        self._parked = False

    async def execute(self, account_id: str, action: TelegramAction) -> ActionResult:
        if len(self.actions) >= self._limit and not self._parked:
            self._parked = True
            self.reached.set()
            await asyncio.Event().wait()
        if self._parked:
            self.unwinding.set()
            await self.release.wait()
        self.actions.append(action.action_type)
        return _ok(account_id, action)


@pytest.mark.asyncio
async def test_a_stop_that_outran_its_cancel_wait_still_gives_the_day_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One Stop, one Start, and a cycle that unwinds too slowly to be waited for (#10).

    The stop path waits ~5s for the cancel and then proceeds regardless, clearing
    ``run_id`` and writing ``idle``. A cycle stuck on a slow proxy hands its
    reservation back after that, so a generation-guarded write was refused and the
    account sat on a phantom "daily limit" until the next UTC midnight — on a routine
    operator Stop then Start.
    """
    _no_quiet_days(monkeypatch)
    monkeypatch.setattr(_runtime, "_warming_loop", _fake_loop)
    monkeypatch.setattr(settings.warming, "stop_cancel_timeout_seconds", 0.05)
    stalled = _StallOnTheWayOut(2)
    monkeypatch.setattr(_seams, "execute", stalled.execute)
    await _seed_warming_account(run_id="run-1")
    task = asyncio.create_task(_iteration("run-1"))
    warming._RUNTIME["acc-1"] = task
    await asyncio.wait_for(stalled.reached.wait(), timeout=5)

    stopped = await warming.stop_warming(StopWarmingRequest(account_id="acc-1"))

    # The stop returned without the cycle: idle, no generation, whole budget booked.
    assert stopped.state == "idle"
    booked = await fetch_warming_state("acc-1")
    assert booked is not None
    assert booked.run_id is None
    assert booked.daily_actions == settings.warming.phase_daily_cap["intro"]

    # Only now does the cleanup finish and the hand-back run.
    await asyncio.wait_for(stalled.unwinding.wait(), timeout=5)
    stalled.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    record = await fetch_warming_state("acc-1")
    assert record is not None
    # set_online + the join really spent, not the 15-action reservation.
    assert record.daily_actions == 2

    # Same calendar day: the operator's Start has the rest of the budget.
    survivor = _Recorder()
    monkeypatch.setattr(_seams, "execute", survivor.execute)
    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))
    restarted = await fetch_warming_state("acc-1")
    assert restarted is not None

    result = await warming.run_loop_iteration("acc-1", run_id=restarted.run_id)

    assert result.detail != "daily limit"
    assert survivor.types()[0] == "set_online"


@pytest.mark.asyncio
async def test_a_start_before_the_dying_cycle_unwinds_still_gets_the_day_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reported click order: Stop, then Start, while the old cycle is still unwinding.

    ``start_warming`` mints the next generation EAGERLY — it stamps ``run_id`` on the
    row before the dying cycle has finished — so by the time the hand-back runs the row
    belongs to run-2 and no generation-based guard can accept it. The booking's own
    token can: run-2 has not booked yet (its first cycle is up to
    ``cold_start_spread_hours`` away), so the token on the row is still ours.
    """
    _no_quiet_days(monkeypatch)
    monkeypatch.setattr(_runtime, "_warming_loop", _fake_loop)
    monkeypatch.setattr(settings.warming, "stop_cancel_timeout_seconds", 0.05)
    stalled = _StallOnTheWayOut(2)
    monkeypatch.setattr(_seams, "execute", stalled.execute)
    await _seed_warming_account(run_id="run-1")
    task = asyncio.create_task(_iteration("run-1"))
    warming._RUNTIME["acc-1"] = task
    await asyncio.wait_for(stalled.reached.wait(), timeout=5)

    await warming.stop_warming(StopWarmingRequest(account_id="acc-1"))
    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))

    restarted = await fetch_warming_state("acc-1")
    assert restarted is not None
    assert restarted.run_id not in (None, "run-1")
    assert restarted.daily_actions == settings.warming.phase_daily_cap["intro"]

    # Only now does the stalled cleanup finish and the hand-back run.
    await asyncio.wait_for(stalled.unwinding.wait(), timeout=5)
    stalled.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.daily_actions == 2

    survivor = _Recorder()
    monkeypatch.setattr(_seams, "execute", survivor.execute)

    result = await warming.run_loop_iteration("acc-1", run_id=restarted.run_id)

    assert result.detail != "daily limit"
    assert survivor.types()[0] == "set_online"


_TOKEN = "booking-1"


_CAP = settings.warming.phase_daily_cap["intro"]
# The next phase up. A real cap value, which is the point: the count a hand-back can
# find on the row is always some generation's saturated booking.
_HIGHER_CAP = settings.warming.phase_daily_cap["warming"]


async def _seed_row(count: int, token: str | None) -> str:
    """One account whose row carries ``count`` against today under ``token``."""
    await create_account(AccountCreate(account_id="acc-1"))
    today = datetime.now(UTC).date().isoformat()
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="active",
            run_id="run-1",
            daily_actions=count,
            daily_count_date=today,
            reservation_token=token,
        ),
    )
    return today


@pytest.mark.parametrize(
    ("row", "spent", "expected"),
    [
        # Applied with budget handed back: the only signal a forfeited day ever
        # produces, since ``_gate_daily_limit`` writes the same ``last_event`` for a
        # legitimate park.
        ((_CAP, _TOKEN), 2, ("warming_reservation_reconciled", 2)),
        # Applied, nothing spent (cancelled while queued on the semaphore):
        # deliberately silent, or every deploy logs one WARNING per account.
        ((_CAP, _TOKEN), 0, (None, 0)),
        # Applied with nothing left over — the cycle spent its whole remaining budget
        # and was then cancelled. Silent: this code says a day was rescued, and an
        # account that simply reached its cap forfeited nothing.
        ((_CAP, _TOKEN), _CAP, (None, _CAP)),
        # Our own applying write cleared the token and the cancel swallowed its result,
        # so the retry finds NULL. Nothing is owed: silent.
        ((2, None), 2, (None, 2)),
        # A newer booking replaced ours, and read its baseline off a row that still
        # carried our booking — our unspent remainder is inside its count now. The
        # reachable route is a phase advance raising the cap: the daily gate parks a
        # generation that finds a saturated count under the same cap, but admits it
        # under a higher one, so it books instead of parking.
        ((_HIGHER_CAP, "booking-2"), 2, ("warming_reservation_absorbed", _HIGHER_CAP)),
        # Our booking, our date, and a count that has GROWN past it: the reservation is
        # still being counted with nobody to release it. No code path is known to
        # produce this — a newer generation either parks (writing the count we booked,
        # which the guard matches) or books (taking the token, i.e. ``absorbed``) — so
        # the fixture is deliberately synthetic and the branch stays fail-loud rather
        # than being assumed away. Its own code, too: ``absorbed`` has a known false
        # positive and this one should never fire, so they must not dilute each other.
        ((_HIGHER_CAP, _TOKEN), 2, ("warming_reservation_stranded", _HIGHER_CAP)),
    ],
)
@pytest.mark.asyncio
async def test_the_reconcile_reports_each_of_its_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    row: tuple[int, str | None],
    spent: int,
    expected: tuple[str | None, int],
) -> None:
    """Every refusal that costs the day is reported, and only those."""
    count, row_token = row
    event, daily_actions = expected
    logged: list[tuple[str, str, object]] = []

    async def capture(level: str, code: str, **kwargs: object) -> None:
        logged.append((level, code, kwargs["extra"]))

    monkeypatch.setattr(_reservation, "log_event", capture)
    today = await _seed_row(count, row_token)

    await _reservation._reconcile_reservation("acc-1", _Reservation(0, today, _CAP, _TOKEN), spent)

    # The hand-back reports its own booking, never the row's count: after a refusal that
    # count can belong to a newer booking, and ``daily_actions`` reads as the row's own.
    losses = {"warming_reservation_absorbed", "warming_reservation_stranded"}
    extra: dict[str, object] = (
        {"spent": spent, "booked": _CAP, "unreleased": _CAP - spent}
        if event in losses
        else {"spent": spent, "daily_actions": spent}
    )
    assert logged == ([("WARNING", event, extra)] if event else [])
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.daily_actions == daily_actions


@pytest.mark.asyncio
async def test_a_repeated_hand_back_neither_lowers_the_count_nor_cries_stranded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Applying clears the token, so the retry is a silent no-op (#10).

    ``asyncio.shield`` leaves a cancelled hand-back landing as a detached task, and
    the loop's exit handler then reconciles the same cycle again. A relative give-back
    would subtract the unspent budget twice and overspend the cap; an absolute write
    guarded by a token that is now gone does nothing at all — and must not report the
    day as forfeited on the way, which is what made this WARNING noise.
    """
    logged: list[str] = []

    async def capture(_level: str, code: str, **_kwargs: object) -> None:
        logged.append(code)

    monkeypatch.setattr(_reservation, "log_event", capture)
    today = await _seed_row(_CAP, _TOKEN)
    booking = _Reservation(0, today, _CAP, _TOKEN)

    await _reservation._reconcile_reservation("acc-1", booking, 2)
    await _reservation._reconcile_reservation("acc-1", booking, 2)

    assert logged == ["warming_reservation_reconciled"]
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.daily_actions == 2
    # Applying retired the token, so a hand-back still naming it cannot match even when
    # its numbers line up with the row exactly — that is the DB-level exactly-once, on
    # top of the count no longer matching what this booking reserved.
    settled = await hand_back_warming_reservation(
        "acc-1", token=_TOKEN, booked=2, reconciled=1, daily_date=today
    )
    assert settled == "settled"


@pytest.mark.asyncio
async def test_a_hand_back_whose_row_rolled_past_midnight_is_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A booking from yesterday must not rewrite — or complain about — a fresh day.

    The row's date rolls forward under our token, because the write that rolls it (a
    park, a gate) does not touch the token. Today's counter starts at zero, so there is
    no budget left to hand back and nothing has been lost: the old predicate called that
    a stranded reservation and warned about a day that had just begun.
    """
    logged: list[str] = []

    async def capture(_level: str, code: str, **_kwargs: object) -> None:
        logged.append(code)

    monkeypatch.setattr(_reservation, "log_event", capture)
    today = await _seed_row(0, _TOKEN)
    yesterday = (datetime.now(UTC).date() - timedelta(days=1)).isoformat()
    assert yesterday != today

    await _reservation._reconcile_reservation("acc-1", _Reservation(0, yesterday, _CAP, _TOKEN), 2)

    assert logged == []
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.daily_actions == 0
    assert record.daily_count_date == today


@pytest.mark.asyncio
async def test_a_cancel_after_a_cycle_spent_its_whole_budget_reports_no_rescue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An account that reached its cap normally must not be told it lost a day (#10).

    Seeded four short of the cap, so a four-action cycle exhausts ``remaining`` exactly
    and the finalize writes the cap. The hand-back that follows a cancel then finds the
    count it was going to write already there and APPLIES — the same value, harmlessly —
    but there was nothing left over, so announcing a rescued day is a false alarm on the
    routine path of an account reaching its cap.
    """
    _no_quiet_days(monkeypatch)
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    logged: list[str] = []

    async def capture(_level: str, code: str, **_kwargs: object) -> None:
        logged.append(code)

    monkeypatch.setattr(_reservation, "log_event", capture)
    real_finalize = _loop._finalize_after_cycle

    async def finalize_then_cancel(  # noqa: PLR0913 - mirrors the real signature exactly
        account_id: str,
        result: WarmingCycleResult,
        age_hours: float,
        reservation: _Reservation,
        schedule: tuple[int, datetime, WarmingState],
        *,
        run_id: str | None,
    ) -> WarmingCycleResult:
        await real_finalize(account_id, result, age_hours, reservation, schedule, run_id=run_id)
        raise asyncio.CancelledError

    monkeypatch.setattr(_loop, "_finalize_after_cycle", finalize_then_cancel)
    await _seed_warming_account()
    await _seed_row(_CAP - 4, None)

    with pytest.raises(asyncio.CancelledError):
        await warming.run_loop_iteration("acc-1")

    assert logged == []
    record = await fetch_warming_state("acc-1")
    assert record is not None
    # Four short of the cap plus the cycle's four actions: at the cap, legitimately.
    assert record.daily_actions == _CAP


@pytest.mark.asyncio
async def test_a_cancel_after_the_finalize_write_reports_no_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The transition already reconciled the count, so the hand-back owes nothing (#10).

    ``_finalize_after_cycle`` writes ``daily_count + actions_done`` and leaves the token
    alone, so a cancel landing after it sends the exit handler's hand-back at a row that
    is already correct. Our token is on it, which the old predicate read as "still
    booked" — it warned that the day was forfeited while quoting the very count that
    proves it was not.
    """
    _no_quiet_days(monkeypatch)
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    logged: list[tuple[str, object]] = []

    async def capture(_level: str, code: str, **kwargs: object) -> None:
        logged.append((code, kwargs.get("extra")))

    monkeypatch.setattr(_reservation, "log_event", capture)
    real_finalize = _loop._finalize_after_cycle

    async def finalize_then_cancel(  # noqa: PLR0913 - mirrors the real signature exactly
        account_id: str,
        result: WarmingCycleResult,
        age_hours: float,
        reservation: _Reservation,
        schedule: tuple[int, datetime, WarmingState],
        *,
        run_id: str | None,
    ) -> WarmingCycleResult:
        # The transition lands, then the cancel arrives — a deploy or a stop whose
        # timing puts it between the finalize write and the loop's next await.
        await real_finalize(account_id, result, age_hours, reservation, schedule, run_id=run_id)
        raise asyncio.CancelledError

    monkeypatch.setattr(_loop, "_finalize_after_cycle", finalize_then_cancel)
    await _seed_warming_account()

    with pytest.raises(asyncio.CancelledError):
        await warming.run_loop_iteration("acc-1")

    assert [code for code, _extra in logged] == []
    record = await fetch_warming_state("acc-1")
    assert record is not None
    # set_online + join + read + the story glance, written by the finalize itself.
    assert record.daily_actions == 4
