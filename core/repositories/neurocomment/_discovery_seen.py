"""Fleet-wide "already shown" set for channel discovery (migration #60).

Backs the search request's ``hide_seen``: every handle a run has ever returned is
recorded once, so a later search can drop what the operator already looked at.

Keyed by ``dedup_key`` on both sides: usernames are case-insensitive and Telegram
returns them in whatever case the owner typed, so storing the raw handle let ``News``
slip past a set that held ``news``. Private ``id:`` refs have no case to fold.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core._schema_tables import _neurocomment_discovery_seen
from core.channel_tokens import dedup_key
from core.db import _get_engine

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

_TABLE = _neurocomment_discovery_seen


def _list_seen(keys: list[str]) -> set[str]:
    if not keys:
        return set()
    statement = select(_TABLE.c.channel).where(_TABLE.c.channel.in_(keys))
    with _get_engine().connect() as connection:
        return {str(row[0]) for row in connection.execute(statement)}


async def list_seen(channels: Iterable[str]) -> set[str]:
    """Which of ``channels`` a run has shown before, as dedup keys."""
    return await asyncio.to_thread(_list_seen, list({dedup_key(c) for c in channels}))


def _mark_seen(keys: list[str], stamp: str) -> None:
    if not keys:
        return
    statement = sqlite_insert(_TABLE)
    statement = statement.on_conflict_do_update(
        index_elements=[_TABLE.c.channel],
        set_={"last_seen_at": statement.excluded.last_seen_at},
    )
    rows = [{"channel": key, "first_seen_at": stamp, "last_seen_at": stamp} for key in keys]
    with _get_engine().begin() as connection:
        connection.execute(statement, rows)


async def mark_seen(channels: Iterable[str], now: datetime) -> None:
    """Record ``channels`` as shown at ``now``; a repeat only moves ``last_seen_at``."""
    keys = list(dict.fromkeys(dedup_key(c) for c in channels))
    await asyncio.to_thread(_mark_seen, keys, now.isoformat())
