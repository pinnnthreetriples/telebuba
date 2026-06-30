"""Operator-editable neurocomment settings — load/save the singleton row (#19).

When no row has been saved, reads return the live ``settings.neurocomment``
config (without persisting), so the engine behaves exactly as before until the
operator saves an override. A save upserts the single row (``id`` pinned to 1).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from sqlalchemy import insert, select, update

from core.config import settings
from core.db import _get_engine, _now_iso
from core.repositories.neurocomment._tables import _neurocomment_settings
from schemas.neurocomment import NeurocommentSettings, NeurocommentSettingsUpdate

if TYPE_CHECKING:
    from collections.abc import Mapping

_SETTINGS_ID = 1


def _config_defaults() -> dict[str, object]:
    nc = settings.neurocomment
    return {
        "max_comments_per_hour": nc.max_comments_per_hour,
        "max_comments_per_channel_per_day": nc.max_comments_per_channel_per_day,
        "reply_delay_min_seconds": nc.reply_delay_min_seconds,
        "reply_delay_max_seconds": nc.reply_delay_max_seconds,
        "min_trust_score": nc.min_trust_score,
        "updated_at": _now_iso(),
    }


def _row_to_settings(mapping: Mapping[str, object]) -> NeurocommentSettings:
    return NeurocommentSettings(
        max_comments_per_hour=int(cast("int", mapping["max_comments_per_hour"])),
        max_comments_per_channel_per_day=int(
            cast("int", mapping["max_comments_per_channel_per_day"]),
        ),
        reply_delay_min_seconds=float(cast("float", mapping["reply_delay_min_seconds"])),
        reply_delay_max_seconds=float(cast("float", mapping["reply_delay_max_seconds"])),
        min_trust_score=int(cast("int", mapping["min_trust_score"])),
        updated_at=str(mapping["updated_at"]),
    )


def _load_neurocomment_settings() -> NeurocommentSettings:
    statement = select(_neurocomment_settings).where(_neurocomment_settings.c.id == _SETTINGS_ID)
    with _get_engine().connect() as connection:
        row = connection.execute(statement).mappings().first()
    if row is None:
        # No saved override → live config (not persisted, so a config/env change
        # takes effect immediately and the engine's prior behaviour is unchanged).
        return _row_to_settings(_config_defaults())
    return _row_to_settings(cast("Mapping[str, object]", row))


async def load_neurocomment_settings() -> NeurocommentSettings:
    """Return the effective neurocomment limits — saved override, else live config."""
    return await asyncio.to_thread(_load_neurocomment_settings)


def _save_neurocomment_settings(data: NeurocommentSettingsUpdate) -> NeurocommentSettings:
    values = {
        "max_comments_per_hour": data.max_comments_per_hour,
        "max_comments_per_channel_per_day": data.max_comments_per_channel_per_day,
        "reply_delay_min_seconds": data.reply_delay_min_seconds,
        "reply_delay_max_seconds": data.reply_delay_max_seconds,
        "min_trust_score": data.min_trust_score,
        "updated_at": _now_iso(),
    }
    with _get_engine().begin() as connection:
        updated = connection.execute(
            update(_neurocomment_settings)
            .where(_neurocomment_settings.c.id == _SETTINGS_ID)
            .values(**values),
        )
        if updated.rowcount == 0:
            connection.execute(insert(_neurocomment_settings).values(id=_SETTINGS_ID, **values))
    return _load_neurocomment_settings()


async def save_neurocomment_settings(data: NeurocommentSettingsUpdate) -> NeurocommentSettings:
    """Persist the operator's neurocomment-settings override (upsert the single row)."""
    return await asyncio.to_thread(_save_neurocomment_settings, data)
