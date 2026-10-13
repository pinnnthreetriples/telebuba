"""``warming_daily_stats`` repository: atomic accumulation, read window, retention."""

from __future__ import annotations

import pytest

from core.repositories.warming_daily_stats import (
    add_warming_daily_stats,
    list_warming_daily_stats,
    purge_warming_daily_stats_older_than,
    sum_warming_daily_stats_by_day,
)
from schemas.warming_daily_stats import WarmingDailyStats


def _increment(day: str = "2026-10-11", **fields: object) -> WarmingDailyStats:
    return WarmingDailyStats.model_validate({"account_id": "acc-1", "day": day, **fields})


@pytest.mark.asyncio
async def test_increments_on_one_day_accumulate_every_counter() -> None:
    await add_warming_daily_stats(
        _increment(cycles=1, reads=3, reactions=2, joins=1, dms=1, phase="intro"),
    )
    await add_warming_daily_stats(
        _increment(cycles=1, reads=2, failures=1, floods=1, phase="settling", trust_score=70),
    )

    [row] = await list_warming_daily_stats("acc-1", "2026-10-01")

    assert row == _increment(
        cycles=2,
        reads=5,
        reactions=2,
        joins=1,
        dms=1,
        failures=1,
        floods=1,
        phase="settling",
        trust_score=70,
    )


@pytest.mark.asyncio
async def test_an_increment_without_a_snapshot_keeps_the_last_one() -> None:
    await add_warming_daily_stats(
        _increment(cycles=1, phase="active", trust_score=80, trust_band="good"),
    )
    await add_warming_daily_stats(_increment(cycles=1))

    [row] = await list_warming_daily_stats("acc-1", "2026-10-11")

    assert (row.cycles, row.phase, row.trust_score, row.trust_band) == (2, "active", 80, "good")


@pytest.mark.asyncio
async def test_the_read_is_one_account_from_a_day_oldest_first() -> None:
    for day in ("2026-10-11", "2026-10-09", "2026-10-10"):
        await add_warming_daily_stats(_increment(day, cycles=1))
    await add_warming_daily_stats(
        WarmingDailyStats(account_id="acc-2", day="2026-10-11", cycles=1),
    )

    rows = await list_warming_daily_stats("acc-1", "2026-10-10")

    assert [(row.account_id, row.day) for row in rows] == [
        ("acc-1", "2026-10-10"),
        ("acc-1", "2026-10-11"),
    ]


@pytest.mark.asyncio
async def test_the_purge_drops_days_before_the_cutoff_date_and_keeps_that_date() -> None:
    for day in ("2025-10-10", "2025-10-11", "2026-10-11"):
        await add_warming_daily_stats(_increment(day, cycles=1))

    removed = await purge_warming_daily_stats_older_than("2025-10-11T12:00:00+00:00")

    assert removed == 1
    rows = await list_warming_daily_stats("acc-1", "2000-01-01")
    assert [row.day for row in rows] == ["2025-10-11", "2026-10-11"]


@pytest.mark.asyncio
async def test_the_fleet_sum_adds_every_account_per_day_from_the_window() -> None:
    await add_warming_daily_stats(_increment("2026-10-09", cycles=9))
    await add_warming_daily_stats(_increment("2026-10-10", cycles=1, reads=2, floods=1))
    await add_warming_daily_stats(
        WarmingDailyStats(account_id="acc-2", day="2026-10-10", cycles=2, dms=1)
    )
    await add_warming_daily_stats(_increment("2026-10-11", reactions=4))

    days = await sum_warming_daily_stats_by_day("2026-10-10")

    assert [(d.day, d.accounts, d.cycles, d.reads, d.dms, d.floods, d.reactions) for d in days] == [
        ("2026-10-10", 2, 3, 2, 1, 1, 0),
        ("2026-10-11", 1, 0, 0, 0, 0, 4),
    ]
