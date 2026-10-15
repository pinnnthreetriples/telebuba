"""Per-account, per-day warming totals — ``warming_daily_stats``.

Counters only ever grow through one atomic UPSERT, so two writers on the same day
cannot lose each other's increment. ``phase`` and the trust fields keep the last
known value: an increment without them leaves the stored ones alone.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core._schema_warming_daily_stats import _warming_daily_stats
from core.db import _get_engine, _now_iso
from schemas.survival import FleetDailyStats
from schemas.warming_daily_stats import WarmingDailyStats

if TYPE_CHECKING:
    from collections.abc import Mapping

_COUNTERS = ("cycles", "reads", "reactions", "joins", "dms", "failures", "floods")
_SNAPSHOT = ("phase", "trust_score", "trust_band")
_DAY_LENGTH = len("YYYY-MM-DD")


def _add_warming_daily_stats(increment: WarmingDailyStats) -> None:
    table = _warming_daily_stats
    values = increment.model_dump()
    values["updated_at"] = _now_iso()
    statement = sqlite_insert(table).values(**values)
    excluded = statement.excluded
    set_: dict[str, Any] = {name: table.c[name] + excluded[name] for name in _COUNTERS}
    set_.update({name: func.coalesce(excluded[name], table.c[name]) for name in _SNAPSHOT})
    set_["updated_at"] = excluded.updated_at
    statement = statement.on_conflict_do_update(
        index_elements=[table.c.account_id, table.c.day],
        set_=set_,
    )
    with _get_engine().begin() as connection:
        connection.execute(statement)


async def add_warming_daily_stats(increment: WarmingDailyStats) -> None:
    """Add one increment to its ``(account_id, day)`` row, creating the row if needed."""
    await asyncio.to_thread(_add_warming_daily_stats, increment)


def _row_to_daily_stats(row: Mapping[str, object]) -> WarmingDailyStats:
    return WarmingDailyStats.model_validate(
        {name: row[name] for name in ("account_id", "day", *_COUNTERS, *_SNAPSHOT)},
    )


def _list_warming_daily_stats(account_id: str, since_day: str) -> list[WarmingDailyStats]:
    table = _warming_daily_stats
    statement = (
        select(table)
        .where((table.c.account_id == account_id) & (table.c.day >= since_day))
        .order_by(table.c.day)
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [_row_to_daily_stats(cast("Mapping[str, object]", row)) for row in rows]


async def list_warming_daily_stats(account_id: str, since_day: str) -> list[WarmingDailyStats]:
    """Rows of one account from ``since_day`` (``YYYY-MM-DD``, inclusive), oldest first."""
    return await asyncio.to_thread(_list_warming_daily_stats, account_id, since_day)


def _list_fleet_warming_daily_stats(since_day: str) -> list[WarmingDailyStats]:
    table = _warming_daily_stats
    statement = select(table).where(table.c.day >= since_day)
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [_row_to_daily_stats(cast("Mapping[str, object]", row)) for row in rows]


async def list_fleet_warming_daily_stats(since_day: str) -> list[WarmingDailyStats]:
    """Every account's rows from ``since_day`` (inclusive), in one read for the board."""
    return await asyncio.to_thread(_list_fleet_warming_daily_stats, since_day)


def _sum_warming_daily_stats_by_day(since_day: str) -> list[FleetDailyStats]:
    table = _warming_daily_stats
    sums = [func.sum(table.c[name]).label(name) for name in _COUNTERS]
    statement = (
        select(table.c.day, func.count(table.c.account_id).label("accounts"), *sums)
        .where(table.c.day >= since_day)
        .group_by(table.c.day)
        .order_by(table.c.day)
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [FleetDailyStats.model_validate(dict(row)) for row in rows]


async def sum_warming_daily_stats_by_day(since_day: str) -> list[FleetDailyStats]:
    """Every account's rows from ``since_day`` (inclusive) added up per day, oldest first."""
    return await asyncio.to_thread(_sum_warming_daily_stats_by_day, since_day)


def _purge_warming_daily_stats_older_than(cutoff_iso: str) -> int:
    statement = delete(_warming_daily_stats).where(
        _warming_daily_stats.c.day < cutoff_iso[:_DAY_LENGTH],
    )
    with _get_engine().begin() as connection:
        return connection.execute(statement).rowcount


async def purge_warming_daily_stats_older_than(cutoff_iso: str) -> int:
    """Delete rows whose day falls before the cutoff's date; returns how many went.

    Takes an ISO timestamp like the other retention purges and keeps its date's row,
    so the cutoff day itself survives.
    """
    return await asyncio.to_thread(_purge_warming_daily_stats_older_than, cutoff_iso)
