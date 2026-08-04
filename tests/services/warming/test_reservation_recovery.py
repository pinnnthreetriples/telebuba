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
from core.db import create_account, fetch_warming_state, upsert_warming_state
from schemas.accounts import AccountCreate
from schemas.warming import StartWarmingRequest, StopWarmingRequest, WarmingStateWrite
from services import warming
from services.warming import _reservation, _runtime, _seams
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


async def _seed_booked_row(booked: int) -> str:
    """One account whose row carries ``booked`` against today, and today's date."""
    await create_account(AccountCreate(account_id="acc-1"))
    today = datetime.now(UTC).date().isoformat()
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="active",
            run_id="run-1",
            daily_actions=booked,
            daily_count_date=today,
        ),
    )
    return today


@pytest.mark.parametrize(
    ("spent", "remaining", "event", "daily_actions"),
    [
        # Applied, real spend: the only signal a forfeited day ever produces, since
        # ``_gate_daily_limit`` writes the same ``last_event`` for a legitimate park.
        (2, 15, "warming_reservation_reconciled", 2),
        # Applied, nothing spent (cancelled while queued on the semaphore):
        # deliberately silent, or every deploy logs one WARNING per account.
        (0, 15, None, 0),
        # The booking on the row is not the one this cycle made, so it stays booked
        # past the next UTC midnight — the case the operator actually has to see.
        (2, 13, "warming_reservation_stranded", 15),
    ],
)
@pytest.mark.asyncio
async def test_the_reconcile_reports_each_of_its_three_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    spent: int,
    remaining: int,
    event: str | None,
    daily_actions: int,
) -> None:
    """#208's two log codes, and the deliberate silence in between."""
    logged: list[tuple[str, str, object]] = []

    async def capture(level: str, code: str, **kwargs: object) -> None:
        logged.append((level, code, kwargs["extra"]))

    monkeypatch.setattr(_reservation, "log_event", capture)
    today = await _seed_booked_row(15)

    await _reservation._reconcile_reservation("acc-1", _Reservation(0, today, remaining), spent)

    expected = [("WARNING", event, {"spent": spent, "daily_actions": spent})] if event else []
    assert logged == expected
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.daily_actions == daily_actions


@pytest.mark.asyncio
async def test_a_repeated_hand_back_cannot_lower_the_count_twice() -> None:
    """The hand-back stays an absolute write, so repeating it changes nothing (#10).

    ``asyncio.shield`` leaves a cancelled hand-back landing as a detached task, and
    the loop's exit handler then reconciles the same cycle again. A relative
    give-back would subtract the unspent budget twice and let the day overspend its
    cap. Repeating the absolute write is a no-op twice over: it declines (its booked
    value is no longer the count) and it would write the same number anyway.
    """
    today = await _seed_booked_row(15)

    await _reservation._reconcile_reservation("acc-1", _Reservation(0, today, 15), 2)
    await _reservation._reconcile_reservation("acc-1", _Reservation(0, today, 15), 2)

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.daily_actions == 2


@pytest.mark.asyncio
async def test_a_hand_back_from_yesterday_leaves_todays_count_alone() -> None:
    """A cycle that booked before midnight must not rewrite the fresh day's counter."""
    today = await _seed_booked_row(4)
    yesterday = (datetime.now(UTC).date() - timedelta(days=1)).isoformat()
    assert yesterday != today

    await _reservation._reconcile_reservation("acc-1", _Reservation(0, yesterday, 4), 1)

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.daily_actions == 4
