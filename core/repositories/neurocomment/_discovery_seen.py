"""Fleet-wide "already shown" set for channel discovery (migration #60).

Backs the search request's ``hide_seen``: every handle a run has ever returned is
recorded once, so a later search can drop what the operator already looked at.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core._schema_tables import _neurocomment_discovery_seen
from core.db import _get_engine

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

_TABLE = _neurocomment_discovery_seen


def _list_seen(channels: list[str]) -> set[str]:
    if not channels:
        return set()
    statement = select(_TABLE.c.channel).where(_TABLE.c.channel.in_(channels))
    with _get_engine().connect() as connection:
        return {str(row[0]) for row in connection.execute(statement)}


async def list_seen(channels: Iterable[str]) -> set[str]:
    """Which of ``channels`` a run has shown before."""
    return await asyncio.to_thread(_list_seen, list(channels))


def _mark_seen(channels: list[str], stamp: str) -> None:
    if not channels:
        return
    statement = sqlite_insert(_TABLE)
    statement = statement.on_conflict_do_update(
        index_elements=[_TABLE.c.channel],
        set_={"last_seen_at": statement.excluded.last_seen_at},
    )
    rows = [{"channel": c, "first_seen_at": stamp, "last_seen_at": stamp} for c in channels]
    with _get_engine().begin() as connection:
        connection.execute(statement, rows)


async def mark_seen(channels: Iterable[str], now: datetime) -> None:
    """Record ``channels`` as shown at ``now``; a repeat only moves ``last_seen_at``."""
    await asyncio.to_thread(_mark_seen, list(dict.fromkeys(channels)), now.isoformat())
