"""The card's "today" breakdown: read off the daily totals, under the budget's own day."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.db import create_account, upsert_warming_state
from core.repositories.warming_daily_stats import (
    add_warming_daily_stats,
    list_warming_daily_stats_for,
)
from schemas.accounts import AccountCreate
from schemas.warming import WarmingStateWrite
from schemas.warming_daily_stats import WarmingDailyStats
from services import warming


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


async def _seed(account_id: str, *, budget_day: str, stats_day: str) -> None:
    await create_account(AccountCreate(account_id=account_id))
    await upsert_warming_state(
        WarmingStateWrite(
            account_id=account_id,
            state="sleeping",
            daily_actions=9,
            daily_count_date=budget_day,
        ),
    )
    await add_warming_daily_stats(
        WarmingDailyStats(
            account_id=account_id, day=stats_day, cycles=2, reads=5, reactions=3, joins=1, dms=0
        ),
    )


@pytest.mark.asyncio
async def test_a_card_shows_what_today_did_by_kind() -> None:
    await _seed("acc-1", budget_day=_today(), stats_day=_today())

    board = await warming.load_board()

    card = next(c for c in board.warming if c.account_id == "acc-1")
    assert card.today is not None
    assert (card.today.reads, card.today.reactions, card.today.joins, card.today.dms) == (
        5,
        3,
        1,
        0,
    )


@pytest.mark.asyncio
async def test_a_row_of_another_day_is_not_shown_as_today() -> None:
    await _seed("acc-1", budget_day=_today(), stats_day="2020-01-01")

    board = await warming.load_board()

    card = next(c for c in board.warming if c.account_id == "acc-1")
    assert card.today is None


@pytest.mark.asyncio
async def test_a_parked_account_keeps_the_breakdown_of_its_budget_day() -> None:
    # Asleep across midnight: the budget day is days old, and its row is still the one.
    await _seed("acc-1", budget_day="2020-01-01", stats_day="2020-01-01")

    board = await warming.load_board()

    card = next(c for c in board.warming if c.account_id == "acc-1")
    assert card.today is not None
    assert card.today.reads == 5


@pytest.mark.asyncio
async def test_the_board_read_returns_exactly_the_asked_pairs() -> None:
    for account, day in (("a", "2026-10-13"), ("b", "2026-10-14"), ("a", "2026-10-14")):
        await add_warming_daily_stats(WarmingDailyStats(account_id=account, day=day, reads=1))

    rows = await list_warming_daily_stats_for([("a", "2026-10-13"), ("b", "2026-10-14")])

    assert sorted((row.account_id, row.day) for row in rows) == [
        ("a", "2026-10-13"),
        ("b", "2026-10-14"),
    ]
    assert await list_warming_daily_stats_for([]) == []
