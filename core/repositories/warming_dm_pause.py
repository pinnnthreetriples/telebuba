"""Fleet DM pause — the ``warming_settings.dm_paused_until`` column.

Kept apart from the settings save path on purpose: the operator's settings form never
writes it, and only the breaker (trip) and the board's «Снять паузу» (lift) do.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import update

from core._schema_tables import _warming_settings
from core.db import _get_engine
from core.repositories._warming_settings import (
    _WARMING_SETTINGS_ID,
    _invalidate_warming_settings_cache,
    _load_warming_settings,
)


def _store_dm_paused_until(value: str) -> None:
    _load_warming_settings()  # Seeds the singleton row when it does not exist yet.
    statement = (
        update(_warming_settings)
        .where(_warming_settings.c.id == _WARMING_SETTINGS_ID)
        .values(dm_paused_until=value)
    )
    with _get_engine().begin() as connection:
        connection.execute(statement)
    _invalidate_warming_settings_cache()


async def store_dm_paused_until(value: str) -> None:
    """Pause fleet DMs until ``value`` (ISO-8601); a past instant means "not paused"."""
    await asyncio.to_thread(_store_dm_paused_until, value)
