"""Warming repository (split out of core.db for #38).

Owns reads/writes of the warming tables — ``warming_channels``, the singleton
``warming_settings`` row, and per-account ``warming_account_state``. Shared
plumbing (engine, table objects, generic row helpers) is imported from
``core.db``; the public async functions are re-exported by ``core.db`` so
existing call sites are unaffected.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING, cast

from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from core.config import settings
from core.db import (
    _get_engine,
    _now_iso,
    _optional_int,
    _optional_str,
    _required_int,
    _warming_account_state,
    _warming_channels,
    _warming_settings,
)
from schemas.warming import (
    WarmingChannel,
    WarmingChannelList,
    WarmingSettingsSecret,
    WarmingState,
    WarmingStateRecord,
    WarmingStateWrite,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_WARMING_SETTINGS_ID = 1


def _row_to_warming_channel(mapping: Mapping[str, object]) -> WarmingChannel:
    return WarmingChannel(
        channel=str(mapping["channel"]),
        label=_optional_str(mapping.get("label")),
        created_at=str(mapping["created_at"]),
    )


def _list_warming_channels() -> WarmingChannelList:
    statement = select(_warming_channels).order_by(_warming_channels.c.id.asc())
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return WarmingChannelList(
        channels=[_row_to_warming_channel(cast("Mapping[str, object]", row)) for row in rows],
    )


async def list_warming_channels() -> WarmingChannelList:
    return await asyncio.to_thread(_list_warming_channels)


def _add_warming_channel(channel: str, label: str | None) -> WarmingChannelList:
    with _get_engine().begin() as connection, suppress(IntegrityError):
        connection.execute(
            insert(_warming_channels).values(
                channel=channel,
                label=label,
                created_at=_now_iso(),
            ),
        )
    return _list_warming_channels()


async def add_warming_channel(channel: str, label: str | None = None) -> WarmingChannelList:
    """Insert a channel (ignored if it already exists) and return the full list."""
    return await asyncio.to_thread(_add_warming_channel, channel, label)


def _remove_warming_channel(channel: str) -> WarmingChannelList:
    with _get_engine().begin() as connection:
        connection.execute(
            delete(_warming_channels).where(_warming_channels.c.channel == channel),
        )
    return _list_warming_channels()


async def remove_warming_channel(channel: str) -> WarmingChannelList:
    return await asyncio.to_thread(_remove_warming_channel, channel)


def _bool_or(value: object, default: bool) -> bool:  # noqa: FBT001
    return default if value is None else bool(value)


def _int_or(value: object, default: int) -> int:
    return default if value is None else int(cast("int | str", value))


def _row_to_warming_settings_secret(mapping: Mapping[str, object]) -> WarmingSettingsSecret:
    # Columns added after the row was first created are nullable; a NULL means
    # "never set", so fall back to the config default to preserve old behaviour.
    warm = settings.warming
    return WarmingSettingsSecret(
        inter_account_chat=bool(mapping["inter_account_chat"]),
        reactions_enabled=bool(mapping["reactions_enabled"]),
        join_enabled=_bool_or(mapping.get("join_enabled"), default=True),
        enforce_readiness=_bool_or(mapping.get("enforce_readiness"), warm.enforce_readiness),
        quiet_hours_enabled=_bool_or(mapping.get("quiet_hours_enabled"), warm.quiet_hours_enabled),
        quiet_hours_start=_int_or(mapping.get("quiet_hours_start"), warm.quiet_hours_start),
        quiet_hours_end=_int_or(mapping.get("quiet_hours_end"), warm.quiet_hours_end),
        max_daily_actions=_int_or(mapping.get("max_daily_actions"), warm.max_daily_actions),
        gemini_api_key=str(mapping["gemini_api_key"]),
        gemini_model=str(mapping["gemini_model"]),
        updated_at=str(mapping["updated_at"]),
    )


def _default_warming_settings_values() -> dict[str, object]:
    warm = settings.warming
    return {
        "id": _WARMING_SETTINGS_ID,
        "inter_account_chat": 0,
        "reactions_enabled": 1,
        "join_enabled": 1,
        "enforce_readiness": int(warm.enforce_readiness),
        "quiet_hours_enabled": int(warm.quiet_hours_enabled),
        "quiet_hours_start": warm.quiet_hours_start,
        "quiet_hours_end": warm.quiet_hours_end,
        "max_daily_actions": warm.max_daily_actions,
        "gemini_api_key": settings.gemini.api_key,
        "gemini_model": settings.gemini.model,
        "updated_at": _now_iso(),
    }


def _load_warming_settings() -> WarmingSettingsSecret:
    statement = select(_warming_settings).where(_warming_settings.c.id == _WARMING_SETTINGS_ID)
    with _get_engine().begin() as connection:
        row = connection.execute(statement).mappings().first()
        if row is None:
            values = _default_warming_settings_values()
            connection.execute(insert(_warming_settings).values(**values))
            return _row_to_warming_settings_secret(cast("Mapping[str, object]", values))
    return _row_to_warming_settings_secret(cast("Mapping[str, object]", row))


async def load_warming_settings() -> WarmingSettingsSecret:
    """Return the singleton warming settings row, creating defaults on first read."""
    return await asyncio.to_thread(_load_warming_settings)


def _save_warming_settings(  # noqa: PLR0913 - one explicit column per setting reads clearer.
    *,
    inter_account_chat: bool,
    reactions_enabled: bool,
    join_enabled: bool = True,
    enforce_readiness: bool = True,
    quiet_hours_enabled: bool = False,
    quiet_hours_start: int = 0,
    quiet_hours_end: int = 0,
    max_daily_actions: int = 0,
    gemini_api_key: str | None,
    gemini_model: str | None = None,
) -> WarmingSettingsSecret:
    current = _load_warming_settings()
    new_key = current.gemini_api_key if gemini_api_key is None else gemini_api_key
    new_model = gemini_model or current.gemini_model
    values: dict[str, object] = {
        "inter_account_chat": int(inter_account_chat),
        "reactions_enabled": int(reactions_enabled),
        "join_enabled": int(join_enabled),
        "enforce_readiness": int(enforce_readiness),
        "quiet_hours_enabled": int(quiet_hours_enabled),
        "quiet_hours_start": quiet_hours_start,
        "quiet_hours_end": quiet_hours_end,
        "max_daily_actions": max_daily_actions,
        "gemini_api_key": new_key,
        "gemini_model": new_model,
        "updated_at": _now_iso(),
    }
    with _get_engine().begin() as connection:
        connection.execute(
            update(_warming_settings)
            .where(_warming_settings.c.id == _WARMING_SETTINGS_ID)
            .values(**values),
        )
    return _load_warming_settings()


async def save_warming_settings(  # noqa: PLR0913 - mirrors the explicit column list.
    *,
    inter_account_chat: bool,
    reactions_enabled: bool,
    join_enabled: bool = True,
    enforce_readiness: bool = True,
    quiet_hours_enabled: bool = False,
    quiet_hours_start: int = 0,
    quiet_hours_end: int = 0,
    max_daily_actions: int = 0,
    gemini_api_key: str | None,
    gemini_model: str | None = None,
) -> WarmingSettingsSecret:
    """Persist warming settings.

    ``gemini_api_key=None`` leaves the stored key intact; empty string clears it.
    ``gemini_model=None`` or empty leaves the stored model intact.
    """
    return await asyncio.to_thread(
        _save_warming_settings,
        inter_account_chat=inter_account_chat,
        reactions_enabled=reactions_enabled,
        join_enabled=join_enabled,
        enforce_readiness=enforce_readiness,
        quiet_hours_enabled=quiet_hours_enabled,
        quiet_hours_start=quiet_hours_start,
        quiet_hours_end=quiet_hours_end,
        max_daily_actions=max_daily_actions,
        gemini_api_key=gemini_api_key,
        gemini_model=gemini_model,
    )


def _row_to_warming_state_record(mapping: Mapping[str, object]) -> WarmingStateRecord:
    return WarmingStateRecord(
        account_id=str(mapping["account_id"]),
        state=cast("WarmingState", mapping["state"]),
        cycles_completed=_required_int(mapping["cycles_completed"]),
        last_event=_optional_str(mapping.get("last_event")),
        last_cycle_at=_optional_str(mapping.get("last_cycle_at")),
        next_run_at=_optional_str(mapping.get("next_run_at")),
        updated_at=str(mapping["updated_at"]),
        last_error=_optional_str(mapping.get("last_error")),
        last_action=_optional_str(mapping.get("last_action")),
        last_channel=_optional_str(mapping.get("last_channel")),
        heartbeat_at=_optional_str(mapping.get("heartbeat_at")),
        started_at=_optional_str(mapping.get("started_at")),
        stopped_at=_optional_str(mapping.get("stopped_at")),
        flood_wait_seconds=_optional_int(mapping.get("flood_wait_seconds")),
        flood_wait_until=_optional_str(mapping.get("flood_wait_until")),
        proxy_snapshot=_optional_str(mapping.get("proxy_snapshot")),
        daily_actions=_optional_int(mapping.get("daily_actions")) or 0,
        daily_count_date=_optional_str(mapping.get("daily_count_date")),
        quarantine_count=_optional_int(mapping.get("quarantine_count")) or 0,
    )


def _list_warming_states() -> list[WarmingStateRecord]:
    statement = select(_warming_account_state)
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [_row_to_warming_state_record(cast("Mapping[str, object]", row)) for row in rows]


async def list_warming_states() -> list[WarmingStateRecord]:
    return await asyncio.to_thread(_list_warming_states)


def _fetch_warming_state(account_id: str) -> WarmingStateRecord | None:
    statement = select(_warming_account_state).where(
        _warming_account_state.c.account_id == account_id,
    )
    with _get_engine().connect() as connection:
        row = connection.execute(statement).mappings().first()
    if row is None:
        return None
    return _row_to_warming_state_record(cast("Mapping[str, object]", row))


async def fetch_warming_state(account_id: str) -> WarmingStateRecord | None:
    return await asyncio.to_thread(_fetch_warming_state, account_id)


def _upsert_warming_state(data: WarmingStateWrite) -> WarmingStateRecord:
    now = _now_iso()
    values: dict[str, object | None] = {
        "state": data.state,
        "cycles_completed": data.cycles_completed,
        "last_event": data.last_event,
        "last_cycle_at": data.last_cycle_at,
        "next_run_at": data.next_run_at,
        "updated_at": now,
        "last_error": data.last_error,
        "last_action": data.last_action,
        "last_channel": data.last_channel,
        "heartbeat_at": data.heartbeat_at,
        "started_at": data.started_at,
        "stopped_at": data.stopped_at,
        "flood_wait_seconds": data.flood_wait_seconds,
        "flood_wait_until": data.flood_wait_until,
        "proxy_snapshot": data.proxy_snapshot,
        "daily_actions": data.daily_actions,
        "daily_count_date": data.daily_count_date,
        "quarantine_count": data.quarantine_count,
    }
    with _get_engine().begin() as connection:
        exists = connection.execute(
            select(_warming_account_state.c.account_id).where(
                _warming_account_state.c.account_id == data.account_id,
            ),
        ).first()
        if exists is None:
            connection.execute(
                insert(_warming_account_state).values(account_id=data.account_id, **values),
            )
        else:
            connection.execute(
                update(_warming_account_state)
                .where(_warming_account_state.c.account_id == data.account_id)
                .values(**values),
            )
    record = _fetch_warming_state(data.account_id)
    if record is None:
        msg = f"Warming state was not persisted: {data.account_id}"
        raise RuntimeError(msg)
    return record


async def upsert_warming_state(data: WarmingStateWrite) -> WarmingStateRecord:
    return await asyncio.to_thread(_upsert_warming_state, data)
