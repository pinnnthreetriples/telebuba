"""Narrow writes on ``warming_account_state`` for the operator console.

Kept out of the main upsert on purpose: each of these columns has exactly one writer
(a start with geo consent, a hand-off, the live progress rail), and
none of the dozens of state writes in between should carry or clear them. Update-only —
a missing row is left missing, since every writer runs after the row exists.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from sqlalchemy import update

from core.db import _get_engine, _now_iso, _warming_account_state

if TYPE_CHECKING:
    from schemas._warming_console import NcStartMode


def _update(account_id: str, **values: object) -> None:
    table = _warming_account_state
    statement = update(table).where(table.c.account_id == account_id).values(**values)
    with _get_engine().begin() as connection:
        connection.execute(statement)


async def store_geo_ack(account_id: str, pair: str) -> None:
    """Remember the operator's consent to the ``PHONE>PROXY`` country pair."""
    await asyncio.to_thread(_update, account_id, geo_ack=pair)


async def store_nc_start(account_id: str, mode: NcStartMode, at: str) -> None:
    """How (and when) the account was handed to neurocomment."""
    await asyncio.to_thread(_update, account_id, nc_start_mode=mode, nc_handoff_at=at)


def _store_cycle_progress(
    account_id: str, step: str, heartbeat_at: str, expected_run_id: str | None
) -> None:
    table = _warming_account_state
    statement = (
        update(table)
        .where(table.c.account_id == account_id)
        .values(state="active", last_action=step, heartbeat_at=heartbeat_at, updated_at=_now_iso())
    )
    if expected_run_id is not None:
        # The state upsert's CAS: only this generation, never over a stop's ``idle``.
        statement = statement.where(table.c.run_id == expected_run_id, table.c.state != "idle")
    with _get_engine().begin() as connection:
        connection.execute(statement)


async def store_cycle_progress(
    account_id: str,
    step: str,
    heartbeat_at: str,
    expected_run_id: str | None,
) -> None:
    """Advance the running cycle's progress rail — one UPDATE, no read (item 5.1).

    Touches only what the rail shows, so it needs no read of the row to carry the
    rest; ``expected_run_id`` fences a retired generation out like the upsert's CAS.
    """
    await asyncio.to_thread(_store_cycle_progress, account_id, step, heartbeat_at, expected_run_id)
