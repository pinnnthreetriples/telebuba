"""Persisted neurocomment runtime state — the remembered listener + run flag.

Two scalars survive a restart in the single-row ``neurocomment_runtime`` table
(``id`` pinned to 1, mirroring the warming-settings singleton):

- ``listener_account_id`` — *which* account is the listener. ``None`` only when
  the operator explicitly removes the listener ("снять слушателя").
- ``listener_running`` — whether the runtime is *actively subscribed*. A paused
  runtime keeps its remembered ``listener_account_id`` while this is ``False`` so
  the strip survives a reload and ``reconcile_neurocomment_on_startup`` does not
  auto-resume a listener the operator paused (audit 2026-07-02).
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


def _get_listener_running() -> bool:
    statement = select(_neurocomment_runtime.c.listener_running).where(
        _neurocomment_runtime.c.id == _RUNTIME_ROW_ID,
    )
    with _get_engine().connect() as connection:
        row = connection.execute(statement).first()
    return bool(row[0]) if row is not None else False


async def get_listener_running() -> bool:
    """Whether the runtime is actively subscribed (``False`` when paused/stopped)."""
    return await asyncio.to_thread(_get_listener_running)


def _set_listener_running(*, running: bool) -> None:
    fields = {"listener_running": running, "updated_at": _now_iso()}
    statement = (
        sqlite_insert(_neurocomment_runtime)
        .values(id=_RUNTIME_ROW_ID, **fields)
        .on_conflict_do_update(index_elements=[_neurocomment_runtime.c.id], set_=fields)
    )
    with _get_engine().begin() as connection:
        connection.execute(statement)


async def set_listener_running(*, running: bool) -> None:
    """Persist whether the runtime is actively subscribed (pause/resume flag)."""
    await asyncio.to_thread(_set_listener_running, running=running)
