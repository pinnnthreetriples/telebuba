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
    _warming_account_state,
    _warming_channels,
    _warming_joined_channels,
)

# The singleton ``warming_settings`` persistence and the reservation hand-back write
# live in sibling modules for the file-size budget; re-exported here (and thence by
# ``core.db``) so existing call sites keep importing them from
# ``core.repositories.warming``.
from core.repositories._warming_reservation import (  # noqa: F401
    hand_back_warming_reservation,
)
from core.repositories._warming_rows import _row_to_warming_state_record
from core.repositories._warming_settings import (  # noqa: F401
    load_warming_settings,
    save_warming_settings,
)
from schemas.warming import (
    WarmingChannel,
    WarmingChannelList,
    WarmingChannelObservation,
    WarmingStateRecord,
    WarmingStateWrite,
    WarmingStateWriteResult,
    is_warming,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


def _row_to_warming_channel(mapping: Mapping[str, object]) -> WarmingChannel:
    return WarmingChannel.model_validate(
        {
            "channel": str(mapping["channel"]),
            "label": _optional_str(mapping.get("label")),
            "created_at": str(mapping["created_at"]),
            "language": _optional_str(mapping.get("language")),
            "country": _optional_str(mapping.get("country")),
            "kind": _optional_str(mapping.get("kind")),
            "subscribers": _optional_int(mapping.get("subscribers")),
            "last_post_at": _optional_str(mapping.get("last_post_at")),
            "source": _optional_str(mapping.get("source")) or "manual",
            "status": _optional_str(mapping.get("status")) or "approved",
            "found_by": _optional_str(mapping.get("found_by")),
        },
    )


def _list_warming_channels() -> WarmingChannelList:
    # A found channel the operator removed stays as ``removed`` and is never listed.
    approved = _warming_channels.c.status == "approved"
    statement = select(_warming_channels).where(approved).order_by(_warming_channels.c.id.asc())
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
    """Drop an operator's own channel; keep a found one as ``removed``.

    A search would otherwise find the removed channel again and put it straight back,
    since nothing waits for approval any more.
    """
    row = _warming_channels.c.channel == channel
    with _get_engine().begin() as connection:
        connection.execute(
            delete(_warming_joined_channels).where(_warming_joined_channels.c.channel == channel),
        )
        connection.execute(
            delete(_warming_channels).where(row, _warming_channels.c.source == "manual")
        )
        connection.execute(update(_warming_channels).where(row).values(status="removed"))
    return _list_warming_channels()


async def remove_warming_channel(channel: str) -> WarmingChannelList:
    return await asyncio.to_thread(_remove_warming_channel, channel)


_LABEL_COLUMNS = frozenset({"language", "country"})


def _label_warming_channel(channel: str, labels: Mapping[str, object]) -> bool:
    row = _warming_channels.c.channel == channel
    values = {name: value for name, value in labels.items() if name in _LABEL_COLUMNS}
    with _get_engine().begin() as connection:
        if values:
            result = connection.execute(update(_warming_channels).where(row).values(values))
            return result.rowcount > 0
        found = connection.execute(select(_warming_channels.c.id).where(row)).first()
        return found is not None


async def label_warming_channel(channel: str, labels: Mapping[str, object]) -> bool:
    """Write only the given language/country labels; ``False`` when the channel is unknown."""
    return await asyncio.to_thread(_label_warming_channel, channel, labels)


def _observe_warming_channel(channel: str, observed: WarmingChannelObservation) -> None:
    table = _warming_channels
    row = table.c.channel == channel
    with _get_engine().begin() as connection:
        if observed.last_post_at is not None:
            connection.execute(update(table).where(row).values(last_post_at=observed.last_post_at))
        # A label the operator (or an earlier read) already set is never overwritten.
        if observed.language is not None:
            unset = table.c.language.is_(None)
            connection.execute(update(table).where(row, unset).values(language=observed.language))
        if observed.kind is not None:
            unset = table.c.kind.is_(None)
            connection.execute(update(table).where(row, unset).values(kind=observed.kind))


async def observe_warming_channel(channel: str, observed: WarmingChannelObservation) -> None:
    """Record what a warming read learnt about a channel; fills only unknown labels."""
    await asyncio.to_thread(_observe_warming_channel, channel, observed)


def _list_warming_states() -> list[WarmingStateRecord]:
    statement = select(_warming_account_state)
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [_row_to_warming_state_record(cast("Mapping[str, object]", row)) for row in rows]


async def list_warming_states() -> list[WarmingStateRecord]:
    return await asyncio.to_thread(_list_warming_states)


def _list_warming_states_by_ids(account_ids: list[str]) -> list[WarmingStateRecord]:
    if not account_ids:
        return []
    statement = select(_warming_account_state).where(
        _warming_account_state.c.account_id.in_(account_ids),
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [_row_to_warming_state_record(cast("Mapping[str, object]", row)) for row in rows]


async def list_warming_states_by_ids(account_ids: list[str]) -> list[WarmingStateRecord]:
    """Warming states for a specific set of accounts (bulk read for the board)."""
    return await asyncio.to_thread(_list_warming_states_by_ids, account_ids)


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


def _mark_nc_handed_off(account_id: str) -> None:
    """Set the second hand-off flag: warmed card → neurocomment idle pool.

    Update-only and gated on ``promoted_to_nc`` — handing off an account that
    is not on the warmed card is a caller error, surfaced as ``ValueError``
    (same contract as ``_mark_promoted_to_nc``'s unknown-account guard).
    """
    now = _now_iso()
    statement = (
        update(_warming_account_state)
        .where(
            _warming_account_state.c.account_id == account_id,
            _warming_account_state.c.promoted_to_nc == 1,
        )
        .values(nc_handed_off=1, updated_at=now)
    )
    with _get_engine().begin() as connection:
        result = connection.execute(statement)
        if result.rowcount == 0:
            msg = f"account is not in the warmed pool: {account_id!r}"
            raise ValueError(msg)


async def mark_nc_handed_off(account_id: str) -> None:
    """Hand a graduated account to the neurocomment idle pool (operator action)."""
    await asyncio.to_thread(_mark_nc_handed_off, account_id)


def _mark_unpromoted(account_id: str) -> None:
    """Clear the graduation + hand-off flags (Bug 2: stops the dual-pool leak).

    Update-only — un-promoting an account that was never promoted is a no-op,
    not a chance to create a stub row. Clears BOTH flags: a returned-to-warming
    account must not linger in the neurocomment idle pool.
    """
    now = _now_iso()
    statement = (
        update(_warming_account_state)
        .where(_warming_account_state.c.account_id == account_id)
        .values(promoted_to_nc=0, nc_handed_off=0, updated_at=now)
    )
    with _get_engine().begin() as connection:
        connection.execute(statement)


async def unmark_promoted_to_nc(account_id: str) -> None:
    """Reverse a graduation: clear ``promoted_to_nc`` on the warming-state row."""
    await asyncio.to_thread(_mark_unpromoted, account_id)


def _left_alone_unless_given(data: WarmingStateWrite) -> dict[str, object | None]:
    """The columns a write sets only when it carries them (``None`` = leave as is)."""
    values: dict[str, object | None] = {}
    if data.reservation_token is not None:
        # #10: the only writer is the ``cycle_started`` booking. Absent means "leave
        # the column alone" — see WarmingStateWrite.reservation_token — so listing it
        # unconditionally would have every unrelated write NULL out the token the
        # hand-back guards on, which is the stranded day all over again.
        values["reservation_token"] = data.reservation_token
    if data.cycle_marks is not None:
        # Written with the booking, under its CAS, so the card never shows one
        # cycle's plan on top of another's budget.
        values["cycle_base_actions"] = data.cycle_marks.base_actions
        values["visit_kind"] = data.cycle_marks.visit_kind
        values["cycle_plan"] = ",".join(data.cycle_marks.steps)
    if data.warmed_days is not None:  # only a fresh stint; daily counts use _warming_days
        values["warmed_days"] = data.warmed_days
        values["last_warmed_date"] = data.last_warmed_date
    if data.device_mode is not None:
        # Written by a start only; every other write leaves the stint's device alone.
        values["device_mode"] = data.device_mode
    if data.wave is not None:  # a start only, under the same CAS as its stint stamp
        values["wave"] = data.wave
    if data.spam_blocked_at is not None:  # the spam-block stop and keeping that account
        values["spam_blocked_at"] = data.spam_blocked_at
    return values


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
        "recovered_at": data.recovered_at,
    }
    insert_values.update(_left_alone_unless_given(data))
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
    select_stmt = select(_warming_account_state).where(
        _warming_account_state.c.account_id == data.account_id,
    )
    with _get_engine().begin() as connection:
        # RETURNING hands back the written row (incl. the atomic ``cycles_completed``
        # bump) in the same statement: the INSERT branch and a matching ON CONFLICT
        # DO UPDATE return it, an UPDATE the CAS ``WHERE`` rejected returns nothing.
        row = connection.execute(stmt.returning(*_warming_account_state.c)).mappings().first()
        applied = row is not None
        if row is None:
            # Rejected: report the row the other generation left, read on the same
            # connection inside the same transaction.
            row = connection.execute(select_stmt).mappings().first()
    if row is None:
        msg = f"Warming state was not persisted: {data.account_id}"
        raise RuntimeError(msg)
    record = _row_to_warming_state_record(cast("Mapping[str, object]", row))
    return WarmingStateWriteResult(record=record, applied=applied)


async def upsert_warming_state(data: WarmingStateWrite) -> WarmingStateWriteResult:
    return await asyncio.to_thread(_upsert_warming_state, data)
