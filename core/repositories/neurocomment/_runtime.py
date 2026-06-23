"""Persisted neurocomment runtime state — the active listener account id (issue #119).

One scalar survives a restart so the engine's ``reconcile_neurocomment_runtime``
can re-point the listener at boot. Stored in a single-row table
(``neurocomment_runtime``, ``id`` pinned to 1), mirroring the warming-settings
singleton pattern. ``None`` means the listener is stopped.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core.db import _get_engine, _now_iso
from core.repositories.neurocomment._tables import _neurocomment_runtime

_RUNTIME_ROW_ID = 1


def _get_listener_account_id() -> str | None:
    statement = select(_neurocomment_runtime.c.listener_account_id).where(
        _neurocomment_runtime.c.id == _RUNTIME_ROW_ID,
    )
    with _get_engine().connect() as connection:
        row = connection.execute(statement).first()
    return None if row is None else row[0]


async def get_listener_account_id() -> str | None:
    """The persisted active listener account id, or ``None`` when stopped."""
    return await asyncio.to_thread(_get_listener_account_id)


def _set_listener_account_id(account_id: str | None) -> None:
    fields = {"listener_account_id": account_id, "updated_at": _now_iso()}
    statement = (
        sqlite_insert(_neurocomment_runtime)
        .values(id=_RUNTIME_ROW_ID, **fields)
        .on_conflict_do_update(index_elements=[_neurocomment_runtime.c.id], set_=fields)
    )
    with _get_engine().begin() as connection:
        connection.execute(statement)


async def set_listener_account_id(account_id: str | None) -> None:
    """Persist (or clear, with ``None``) the active listener account id."""
    await asyncio.to_thread(_set_listener_account_id, account_id)
