"""The warming pace card's choice — ``warming_settings.tuning_preset`` / ``tuning_custom``.

Kept apart from the settings save path, like the fleet DM pause: the settings form
never writes these columns, and only ``PUT /warming/tuning`` does. The write drops
the settings cache, so the engine's next read sees the new pace (5.2).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from sqlalchemy import update

from core._schema_tables import _warming_settings
from core.db import _get_engine, _now_iso
from core.repositories._warming_settings import (
    _WARMING_SETTINGS_ID,
    _invalidate_warming_settings_cache,
    _load_warming_settings,
)

if TYPE_CHECKING:
    from schemas._warming_tuning import StoredTuning


def _store_warming_tuning(tuning: StoredTuning) -> None:
    _load_warming_settings()  # Seeds the singleton row when it does not exist yet.
    custom = tuning.custom.model_dump_json() if tuning.custom is not None else None
    statement = (
        update(_warming_settings)
        .where(_warming_settings.c.id == _WARMING_SETTINGS_ID)
        .values(tuning_preset=tuning.preset, tuning_custom=custom, updated_at=_now_iso())
    )
    with _get_engine().begin() as connection:
        connection.execute(statement)
    _invalidate_warming_settings_cache()


async def store_warming_tuning(tuning: StoredTuning) -> None:
    """Replace the stored preset and «Свой» limits with ``tuning``."""
    await asyncio.to_thread(_store_warming_tuning, tuning)


def cached_warming_tuning() -> StoredTuning:
    """The stored pace from the settings row's in-process cache — synchronous on purpose.

    The engine's pure math (``services.warming._tuning``) reads it many times a second
    from synchronous code. Every iteration loads the row through ``load_warming_settings``
    first, so this is a cache hit; only a cold cache (start-up, or right after a settings
    write) reads the single row here.
    """
    return _load_warming_settings().tuning
