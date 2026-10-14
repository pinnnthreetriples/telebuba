"""A cycle's booking keeps the settled count it booked on top of (item 2.3, «15/15» fix)."""

from __future__ import annotations

import pytest

from core.db import create_account, fetch_warming_state, upsert_warming_state
from schemas._warming_visits import CycleMarks
from schemas.accounts import AccountCreate
from schemas.warming import WarmingStateWrite
from services import warming
from services.warming import _iteration


@pytest.mark.asyncio
async def test_the_booking_stores_the_settled_base_and_the_board_shows_it() -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="active",
            daily_actions=6,
            daily_count_date="2026-06-13",
        ),
    )
    plan = _iteration._IterationPlan(
        age_hours=500.0,
        effective_cap=15,
        daily=(6, "2026-06-13"),
        remaining=9,
        dm_allowed=False,
        persona="normal",
    )

    reserved = await _iteration._reserve_iteration("acc-1", plan, None)

    assert isinstance(reserved, _iteration._ReservedIteration)
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert (record.daily_actions, record.cycle_base_actions) == (15, 6)
    (card,) = (await warming.load_board()).warming
    # The whole reservation sits on the row, the card shows what was really spent.
    assert (card.daily_actions, card.daily_settled) == (15, 6)


@pytest.mark.asyncio
async def test_an_older_booking_cannot_write_its_base_over_a_newer_one() -> None:
    """The marks ride the booking's own CAS: a retired generation's booking is refused whole."""
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="active",
            run_id="newer",
            reservation_token="newer",
            cycle_marks=CycleMarks(base_actions=7, visit_kind="binge", steps=["set_online"]),
        ),
    )

    stale = await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="active",
            run_id="older",
            expected_run_id="older",
            reservation_token="older",
            cycle_marks=CycleMarks(base_actions=3, visit_kind="glance", steps=["set_online"]),
        ),
    )

    assert stale.applied is False
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert (record.cycle_base_actions, record.visit_kind) == (7, "binge")
