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
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError

from core.db import (
    _accounts,
    _get_engine,
    _now_iso,
    _optional_int,
    _optional_str,
    _required_int,
    _warming_account_state,
    _warming_channels,
    _warming_joined_channels,
)

# The singleton ``warming_settings`` persistence lives in a sibling module for
# the file-size budget; re-exported here (and thence by ``core.db``) so existing
# call sites keep importing it from ``core.repositories.warming``.
from core.repositories._warming_settings import (  # noqa: F401
    load_warming_settings,
    save_warming_settings,
)
from schemas.warming import (
    ActivityPersona,
    WarmingChannel,
    WarmingChannelList,
    WarmingPhase,
    WarmingState,
    WarmingStateRecord,
    WarmingStateWrite,
    WarmingStateWriteResult,
    is_warming,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


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
            delete(_warming_joined_channels).where(_warming_joined_channels.c.channel == channel),
        )
        connection.execute(
            delete(_warming_channels).where(_warming_channels.c.channel == channel),
        )
    return _list_warming_channels()


async def remove_warming_channel(channel: str) -> WarmingChannelList:
    return await asyncio.to_thread(_remove_warming_channel, channel)


def _row_to_warming_state_record(mapping: Mapping[str, object]) -> WarmingStateRecord:
    phase_raw = _optional_str(mapping.get("current_phase"))
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
        run_id=_optional_str(mapping.get("run_id")),
        current_phase=cast("WarmingPhase | None", phase_raw),
        phase_entered_at=_optional_str(mapping.get("phase_entered_at")),
        promoted_to_nc=bool(mapping.get("promoted_to_nc") or 0),
        target_days=_optional_int(mapping.get("target_days")),
        activity_persona=cast(
            "ActivityPersona",
            _optional_str(mapping.get("activity_persona")) or "normal",
        ),
    )


def _list_warming_states() -> list[WarmingStateRecord]:
    statement = select(_warming_account_state)
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [_row_to_warming_state_record(cast("Mapping[str, object]", row)) for row in rows]


async def list_warming_states() -> list[WarmingStateRecord]:
    return await asyncio.to_thread(_list_warming_states)


async def list_warming_account_ids() -> set[str]:
    """Ids of accounts currently in the warming runtime (any active warming state).

    The one authoritative definition of "this account is busy warming" — shared by
    the neurocomment listener guard and the dialogue-partner pool so the set of
    blocking states cannot drift between call sites.
    """
    return {record.account_id for record in await list_warming_states() if is_warming(record.state)}


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


def _mark_promoted_to_nc(account_id: str) -> None:
    """Set the operator graduation flag on the account's warming-state row.

    Insert-or-update: a brand-new account (no warming row yet) gets a stub row
    in ``idle`` so the flag survives the first card render. Existing rows are
    flipped to True without touching any other column.

    Bug 14: SQLite FKs are off in some test paths and the upsert would happily
    create a ghost row for any string. Validate the account exists up front so
    callers get a typed error instead of silent corruption.
    """
    now = _now_iso()
    exists_stmt = select(_accounts.c.account_id).where(_accounts.c.account_id == account_id)
    with _get_engine().begin() as connection:
        if connection.execute(exists_stmt).first() is None:
            msg = f"unknown account_id: {account_id!r}"
            raise ValueError(msg)
        statement = (
            sqlite_insert(_warming_account_state)
            .values(
                account_id=account_id,
                state="idle",
                cycles_completed=0,
                updated_at=now,
                promoted_to_nc=1,
            )
            .on_conflict_do_update(
                index_elements=[_warming_account_state.c.account_id],
                set_={"promoted_to_nc": 1, "updated_at": now},
            )
        )
        connection.execute(statement)


async def mark_promoted_to_nc(account_id: str) -> None:
    """Promote an account out of warming into the neurocomment pool (operator action)."""
    await asyncio.to_thread(_mark_promoted_to_nc, account_id)


def _mark_unpromoted(account_id: str) -> None:
    """Clear the operator graduation flag (Bug 2: stops the dual-pool leak).

    Update-only — un-promoting an account that was never promoted is a no-op,
    not a chance to create a stub row.
    """
    now = _now_iso()
    statement = (
        update(_warming_account_state)
        .where(_warming_account_state.c.account_id == account_id)
        .values(promoted_to_nc=0, updated_at=now)
    )
    with _get_engine().begin() as connection:
        connection.execute(statement)


async def unmark_promoted_to_nc(account_id: str) -> None:
    """Reverse a graduation: clear ``promoted_to_nc`` on the warming-state row."""
    await asyncio.to_thread(_mark_unpromoted, account_id)


def _upsert_warming_state(data: WarmingStateWrite) -> WarmingStateWriteResult:
    # F9 + P1.2 + P2.4 + Round-2 P1 + Round-4 P1.1/P1.2: collapse to a single
    # sqlite_insert ON CONFLICT DO UPDATE so the whole upsert runs under
    # SQLite's implicit write lock, eliminating the select-then-write TOCTOU.
    # ``run_id`` carries the loop generation marker, ``increment_cycle=True``
    # makes the cycles_completed bump an atomic SQL expression, and
    # ``expected_run_id`` turns the UPDATE branch into a CAS: the row is only
    # mutated when its current ``run_id`` matches what the caller saw AND the
    # row is not already in ``idle``. Returns ``WarmingStateWriteResult`` so
    # the caller can detect a CAS no-op via ``applied=False`` (R4-P1.2) — the
    # iteration uses that signal to abort before doing Telegram I/O on behalf
    # of a stale generation.
    now = _now_iso()
    insert_values: dict[str, object | None] = {
        "state": data.state,
        # For a brand-new row, increment_cycle just means "this is cycle 1".
        # The caller supplies cycles_completed=1 in that case; otherwise the
        # supplied value is used verbatim.
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
        "run_id": data.run_id,
        "current_phase": data.current_phase,
        "phase_entered_at": data.phase_entered_at,
        "target_days": data.target_days,
        # NOT NULL column — coalesce a carried/absent value to the balanced
        # persona so an explicit NULL write can never violate the constraint.
        "activity_persona": data.activity_persona or "normal",
    }
    update_values: dict[str, object] = dict(insert_values)
    if data.increment_cycle:
        update_values["cycles_completed"] = _warming_account_state.c.cycles_completed + 1
    insert_stmt = sqlite_insert(_warming_account_state).values(
        account_id=data.account_id, **insert_values
    )
    if data.expected_run_id is not None:
        # Round-4 P1.1: belt+suspenders. The first predicate is the
        # generation check; the second rejects any UPDATE that would
        # overwrite a row already in ``idle``. So even if a future caller of
        # _stop_warming forgets to clear run_id, the stale loop's CAS write
        # still degrades to a no-op (the operator's idle wins).
        stmt = insert_stmt.on_conflict_do_update(
            index_elements=[_warming_account_state.c.account_id],
            set_=update_values,
            where=(
                (_warming_account_state.c.run_id == data.expected_run_id)
                & (_warming_account_state.c.state != "idle")
            ),
        )
    else:
        stmt = insert_stmt.on_conflict_do_update(
            index_elements=[_warming_account_state.c.account_id],
            set_=update_values,
        )
    with _get_engine().begin() as connection:
        result = connection.execute(stmt)
        # SQLite reports rowcount=1 for both the INSERT branch and a matching
        # ON CONFLICT DO UPDATE; rowcount=0 when the UPDATE's WHERE rejected.
        applied = result.rowcount > 0
    record = _fetch_warming_state(data.account_id)
    if record is None:
        msg = f"Warming state was not persisted: {data.account_id}"
        raise RuntimeError(msg)
    return WarmingStateWriteResult(record=record, applied=applied)


async def upsert_warming_state(data: WarmingStateWrite) -> WarmingStateWriteResult:
    return await asyncio.to_thread(_upsert_warming_state, data)
