"""Tombstones of removals not yet confirmed: one row per kind and key.

Sync helpers on a worker thread, the ``core.repositories.account_own_authorizations`` shape.
Deliberately NOT tied to the accounts table: the row has to outlive the account it is about.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core._schema_pending_cleanups import _pending_cleanups
from core.db import _get_engine


def _add_pending_cleanup(kind: str, key: str, requested_at: str) -> None:
    statement = (
        sqlite_insert(_pending_cleanups)
        .values(kind=kind, key=key, requested_at=requested_at)
        .on_conflict_do_nothing()
    )
    with _get_engine().begin() as connection:
        connection.execute(statement)


async def add_pending_cleanup(kind: str, key: str, *, requested_at: str) -> None:
    """Record that ``key`` of ``kind`` is to be removed; one already recorded keeps its row."""
    await asyncio.to_thread(_add_pending_cleanup, kind, key, requested_at)


def _clear_pending_cleanup(kind: str, key: str) -> None:
    table = _pending_cleanups
    with _get_engine().begin() as connection:
        connection.execute(delete(table).where(table.c.kind == kind, table.c.key == key))


async def clear_pending_cleanup(kind: str, key: str) -> None:
    """Forget the tombstone: the removal is confirmed."""
    await asyncio.to_thread(_clear_pending_cleanup, kind, key)


def _fetch_pending_cleanups(kind: str) -> list[str]:
    table = _pending_cleanups
    with _get_engine().connect() as connection:
        rows = connection.execute(
            select(table.c.key).where(table.c.kind == kind).order_by(table.c.requested_at)
        ).all()
    return [str(row[0]) for row in rows]


async def fetch_pending_cleanups(kind: str) -> list[str]:
    """The keys of ``kind`` whose removal is still owed, oldest request first."""
    return await asyncio.to_thread(_fetch_pending_cleanups, kind)
