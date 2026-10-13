"""Outreach pacing — the limits singleton, per-day usage counters and saved presets.

Limits: with no saved row the schema defaults are in effect (not persisted), and a
save upserts the single row. Usage counters only grow through one atomic UPSERT, so
the lookup and the bulk-message run, which may overlap, never lose an increment.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError

from core._schema_outreach import _outreach_daily_usage, _outreach_limits, _outreach_presets
from core.db import _get_engine, _now_iso
from schemas.outreach_limits import OutreachDailyUsage, OutreachLimits
from schemas.outreach_presets import OutreachPreset

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from schemas.outreach_limits import OutreachLimitsUpdate
    from schemas.outreach_presets import OutreachPresetWrite

_LIMITS_ID = 1
_COUNTERS = ("contact_imports", "contact_matches", "first_messages")


def _load_outreach_limits() -> OutreachLimits:
    statement = select(_outreach_limits).where(_outreach_limits.c.id == _LIMITS_ID)
    with _get_engine().connect() as connection:
        row = connection.execute(statement).mappings().first()
    if row is None:
        return OutreachLimits()
    values = dict(cast("Mapping[str, object]", row))
    values.pop("id")
    return OutreachLimits.model_validate(values)


async def load_outreach_limits() -> OutreachLimits:
    """The saved limits, or the defaults while the operator has saved none."""
    return await asyncio.to_thread(_load_outreach_limits)


def _save_outreach_limits(data: OutreachLimitsUpdate) -> OutreachLimits:
    values = {**data.model_dump(), "updated_at": _now_iso()}
    with _get_engine().begin() as connection:
        updated = connection.execute(
            update(_outreach_limits).where(_outreach_limits.c.id == _LIMITS_ID).values(**values),
        )
        if updated.rowcount == 0:
            connection.execute(insert(_outreach_limits).values(id=_LIMITS_ID, **values))
    return _load_outreach_limits()


async def save_outreach_limits(data: OutreachLimitsUpdate) -> OutreachLimits:
    """Replace the whole limits row."""
    return await asyncio.to_thread(_save_outreach_limits, data)


def _add_outreach_usage(increment: OutreachDailyUsage) -> None:
    table = _outreach_daily_usage
    statement = sqlite_insert(table).values(**increment.model_dump(), updated_at=_now_iso())
    excluded = statement.excluded
    statement = statement.on_conflict_do_update(
        index_elements=[table.c.account_id, table.c.day],
        set_={
            **{name: table.c[name] + excluded[name] for name in _COUNTERS},
            "updated_at": excluded.updated_at,
        },
    )
    with _get_engine().begin() as connection:
        connection.execute(statement)


async def add_outreach_usage(increment: OutreachDailyUsage) -> None:
    """Add one increment to its ``(account_id, day)`` row, creating the row if needed."""
    await asyncio.to_thread(_add_outreach_usage, increment)


def _list_outreach_usage(account_ids: Sequence[str], day: str) -> dict[str, OutreachDailyUsage]:
    table = _outreach_daily_usage
    statement = select(table).where(table.c.account_id.in_(account_ids) & (table.c.day == day))
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    usage = {
        str(row["account_id"]): OutreachDailyUsage.model_validate(
            {name: row[name] for name in ("account_id", "day", *_COUNTERS)},
        )
        for row in rows
    }
    return {
        account_id: usage.get(account_id) or OutreachDailyUsage(account_id=account_id, day=day)
        for account_id in account_ids
    }


async def list_outreach_usage(
    account_ids: Sequence[str],
    day: str,
) -> dict[str, OutreachDailyUsage]:
    """Each account's totals for ``day``; an account with no row gets zeros."""
    return await asyncio.to_thread(_list_outreach_usage, account_ids, day)


def _list_accounts_with_contact_matches(account_ids: Sequence[str]) -> set[str]:
    table = _outreach_daily_usage
    statement = (
        select(table.c.account_id)
        .where(table.c.account_id.in_(account_ids))
        .group_by(table.c.account_id)
        .having(func.sum(table.c.contact_matches) > 0)
    )
    with _get_engine().connect() as connection:
        return {str(row[0]) for row in connection.execute(statement).all()}


async def list_accounts_with_contact_matches(account_ids: Sequence[str]) -> set[str]:
    """The accounts whose lookups ever found someone, across every stored day."""
    return await asyncio.to_thread(_list_accounts_with_contact_matches, account_ids)


class PresetNameTakenError(ValueError):
    """Another preset already has this name (case-insensitive)."""


def _list_outreach_presets() -> list[OutreachPreset]:
    with _get_engine().connect() as connection:
        rows = connection.execute(select(_outreach_presets)).mappings().all()
    presets = [OutreachPreset.model_validate(dict(row)) for row in rows]
    return sorted(presets, key=lambda preset: preset.name.casefold())


async def list_outreach_presets() -> list[OutreachPreset]:
    """Every saved preset, by name."""
    return await asyncio.to_thread(_list_outreach_presets)


def _fetch_preset(preset_id: str) -> OutreachPreset | None:
    statement = select(_outreach_presets).where(_outreach_presets.c.id == preset_id)
    with _get_engine().connect() as connection:
        row = connection.execute(statement).mappings().first()
    return None if row is None else OutreachPreset.model_validate(dict(row))


def _save_outreach_preset(
    preset_id: str | None, data: OutreachPresetWrite
) -> OutreachPreset | None:
    values = {**data.model_dump(), "name_key": data.name.casefold(), "updated_at": _now_iso()}
    try:
        with _get_engine().begin() as connection:
            if preset_id is None:
                preset_id = uuid4().hex
                connection.execute(insert(_outreach_presets).values(id=preset_id, **values))
            else:
                updated = connection.execute(
                    update(_outreach_presets)
                    .where(_outreach_presets.c.id == preset_id)
                    .values(**values),
                )
                if updated.rowcount == 0:
                    return None
    except IntegrityError as exc:
        raise PresetNameTakenError(data.name) from exc
    return _fetch_preset(preset_id)


async def create_outreach_preset(data: OutreachPresetWrite) -> OutreachPreset:
    """Store a new preset; a taken name raises :class:`PresetNameTakenError`."""
    preset = await asyncio.to_thread(_save_outreach_preset, None, data)
    return cast("OutreachPreset", preset)


async def update_outreach_preset(
    preset_id: str, data: OutreachPresetWrite
) -> OutreachPreset | None:
    """Rename and/or re-value a preset; ``None`` when it does not exist."""
    return await asyncio.to_thread(_save_outreach_preset, preset_id, data)


def _delete_outreach_preset(preset_id: str) -> bool:
    statement = delete(_outreach_presets).where(_outreach_presets.c.id == preset_id)
    with _get_engine().begin() as connection:
        return connection.execute(statement).rowcount > 0


async def delete_outreach_preset(preset_id: str) -> bool:
    """Remove a preset; False when it did not exist."""
    return await asyncio.to_thread(_delete_outreach_preset, preset_id)
