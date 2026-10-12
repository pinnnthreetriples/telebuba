"""Single-column writes on ``warming_account_state`` for the operator console.

Kept out of the main upsert on purpose: each of these columns has exactly one writer
(a start with geo consent, a cycle's booking, a hand-off), and none of the dozens of
state writes in between should carry or clear them. Update-only — a missing row is
left missing, since every writer runs after the row exists.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from sqlalchemy import update

from core.db import _get_engine, _warming_account_state

if TYPE_CHECKING:
    from schemas._warming_console import NcStartMode


def _update(account_id: str, token: str | None = None, **values: object) -> None:
    table = _warming_account_state
    statement = update(table).where(table.c.account_id == account_id).values(**values)
    if token is not None:
        statement = statement.where(table.c.reservation_token == token)
    with _get_engine().begin() as connection:
        connection.execute(statement)


async def store_geo_ack(account_id: str, pair: str) -> None:
    """Remember the operator's consent to the ``PHONE>PROXY`` country pair."""
    await asyncio.to_thread(_update, account_id, geo_ack=pair)


async def store_cycle_base(account_id: str, settled: int, token: str) -> None:
    """Today's settled count when a cycle booked its budget; only while ``token`` is the booking.

    The booking's own token fences it: a newer cycle's booking replaced the token, so a
    late write from an older cycle matches no row.
    """
    await asyncio.to_thread(_update, account_id, token, cycle_base_actions=settled)


async def store_nc_start(account_id: str, mode: NcStartMode, at: str) -> None:
    """How (and when) the account was handed to neurocomment."""
    await asyncio.to_thread(_update, account_id, nc_start_mode=mode, nc_handoff_at=at)
