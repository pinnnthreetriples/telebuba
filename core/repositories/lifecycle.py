"""Account lifecycle journal repository (``account_lifecycle_events``, migration #74).

Append-only. The write snapshots the account's Telegram user id and the start of its
current warming stint IN THE SAME TRANSACTION as the insert (or takes a snapshot the
caller read earlier, before a delete cascade), so a row stays attributable after the
account row — and its warming state — are deleted.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, cast

from sqlalchemy import insert, select

from core._schema_lifecycle import _account_lifecycle_events
from core.db import (
    _accounts,
    _get_engine,
    _now_iso,
    _optional_int,
    _optional_str,
    _warming_account_state,
)
from schemas.lifecycle import LifecycleEvent, LifecycleEventKind, LifecycleSnapshot

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.engine import Connection


def _decode_extra(raw: object) -> dict[str, object]:
    try:
        decoded = json.loads(str(raw))
    except ValueError:
        return {}
    return cast("dict[str, object]", decoded) if isinstance(decoded, dict) else {}


def _row_to_event(mapping: Mapping[str, object]) -> LifecycleEvent:
    return LifecycleEvent(
        id=int(cast("int", mapping["id"])),
        account_id=str(mapping["account_id"]),
        tg_user_id=_optional_int(mapping.get("tg_user_id")),
        stint_started_at=_optional_str(mapping.get("stint_started_at")),
        kind=cast("LifecycleEventKind", str(mapping["kind"])),
        created_at=str(mapping["created_at"]),
        extra=_decode_extra(mapping.get("extra")),
        backfilled=bool(mapping.get("backfilled")),
    )


def _snapshot(connection: Connection, account_id: str) -> LifecycleSnapshot:
    user_id = connection.execute(
        select(_accounts.c.user_id).where(_accounts.c.account_id == account_id),
    ).scalar_one_or_none()
    started_at = connection.execute(
        select(_warming_account_state.c.started_at).where(
            _warming_account_state.c.account_id == account_id,
        ),
    ).scalar_one_or_none()
    return LifecycleSnapshot(
        tg_user_id=_optional_int(user_id), stint_started_at=_optional_str(started_at)
    )


def _fetch_lifecycle_snapshot(account_id: str) -> LifecycleSnapshot:
    with _get_engine().connect() as connection:
        return _snapshot(connection, account_id)


async def fetch_lifecycle_snapshot(account_id: str) -> LifecycleSnapshot:
    """The user id and stint start a journal row would snapshot right now."""
    return await asyncio.to_thread(_fetch_lifecycle_snapshot, account_id)


def _append_lifecycle_event(
    account_id: str,
    kind: LifecycleEventKind,
    extra: dict[str, object],
    snapshot: LifecycleSnapshot | None,
) -> LifecycleEvent:
    with _get_engine().begin() as connection:
        taken = snapshot if snapshot is not None else _snapshot(connection, account_id)
        result = connection.execute(
            insert(_account_lifecycle_events)
            .values(
                account_id=account_id,
                tg_user_id=taken.tg_user_id,
                stint_started_at=taken.stint_started_at,
                kind=kind,
                created_at=_now_iso(),
                extra=json.dumps(extra, default=str, sort_keys=True),
                backfilled=0,
            )
            .returning(*_account_lifecycle_events.c),
        )
        row = result.mappings().one()
    return _row_to_event(cast("Mapping[str, object]", row))


async def append_lifecycle_event(
    account_id: str,
    kind: LifecycleEventKind,
    extra: dict[str, object] | None = None,
    snapshot: LifecycleSnapshot | None = None,
) -> LifecycleEvent:
    """Append one journal row.

    Without ``snapshot`` the account's user id and current stint are read in the insert's
    own transaction; a caller that is about to delete those rows passes one taken earlier.
    """
    return await asyncio.to_thread(_append_lifecycle_event, account_id, kind, extra or {}, snapshot)


def _list_lifecycle_events(account_id: str) -> list[LifecycleEvent]:
    statement = (
        select(_account_lifecycle_events)
        .where(_account_lifecycle_events.c.account_id == account_id)
        .order_by(_account_lifecycle_events.c.id)
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [_row_to_event(cast("Mapping[str, object]", row)) for row in rows]


async def list_lifecycle_events(account_id: str) -> list[LifecycleEvent]:
    """Every journal row for one account id, oldest first — including deleted accounts."""
    return await asyncio.to_thread(_list_lifecycle_events, account_id)
