"""Flood memory repository — the append-only ``warming_flood_events`` table.

One row per PeerFlood the warming loop met. Rows are never purged with the
account, so the lifetime count survives a delete + re-import of the same id.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import func, insert, select

from core._schema_tables import _warming_flood_events
from core.db import _get_engine
from schemas.warming_flood import WarmingFloodEventWrite, WarmingFloodSummary


def _record_warming_flood_event(data: WarmingFloodEventWrite) -> None:
    statement = insert(_warming_flood_events).values(
        account_id=data.account_id,
        kind=data.kind,
        action=data.action,
        created_at=data.created_at,
    )
    with _get_engine().begin() as connection:
        connection.execute(statement)


async def record_warming_flood_event(data: WarmingFloodEventWrite) -> None:
    """Remember one spam block for an account."""
    await asyncio.to_thread(_record_warming_flood_event, data)


def _list_warming_flood_summaries(account_id: str | None) -> dict[str, WarmingFloodSummary]:
    table = _warming_flood_events
    statement = select(
        table.c.account_id,
        func.count().label("flood_total"),
        func.max(table.c.created_at).label("last_flood_at"),
    ).group_by(table.c.account_id)
    if account_id is not None:
        statement = statement.where(table.c.account_id == account_id)
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return {
        str(row["account_id"]): WarmingFloodSummary(
            flood_total=int(row["flood_total"]),
            last_flood_at=str(row["last_flood_at"]) if row["last_flood_at"] else None,
        )
        for row in rows
    }


async def list_warming_flood_summaries() -> dict[str, WarmingFloodSummary]:
    """Every account's flood count and latest flood, in one grouped read (board bulk-load)."""
    return await asyncio.to_thread(_list_warming_flood_summaries, None)


async def fetch_warming_flood_summary(account_id: str) -> WarmingFloodSummary:
    """One account's flood count and latest flood; zero when it never met one."""
    found = await asyncio.to_thread(_list_warming_flood_summaries, account_id)
    return found.get(account_id, WarmingFloodSummary())
