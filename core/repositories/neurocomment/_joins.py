"""Neurocomment channel-join log — record + rolling-window count (join cap).

Backs the per-account daily channel-join cap: Telegram freezes an account after
roughly 20-50 channel joins a day, so both join sites (campaign onboarding and
the listener reconcile) gate on a rolling-24h count before sending a real
``JoinChannel`` RPC. Mirrors the comment-quota reader in ``_quota``: sync helpers
wrapped via ``asyncio.to_thread``, returning ints / ``None`` — never raw rows
(non-negotiable #2). ``core.db`` re-exports these so call sites are unchanged.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from sqlalchemy import func, select

from core.db import _get_engine
from core.repositories.neurocomment._tables import _neurocomment_join_log


def _record_join(account_id: str) -> None:
    statement = _neurocomment_join_log.insert().values(
        account_id=account_id,
        joined_at=datetime.now(UTC).isoformat(),
    )
    with _get_engine().begin() as connection:
        connection.execute(statement)


async def record_join(account_id: str) -> None:
    """Stamp one successful channel join for ``account_id`` (now, UTC isoformat)."""
    await asyncio.to_thread(_record_join, account_id)


def _count_account_joins_since(account_id: str, since_iso: str) -> int:
    statement = select(func.count()).where(
        (_neurocomment_join_log.c.account_id == account_id)
        & (_neurocomment_join_log.c.joined_at >= since_iso),
    )
    with _get_engine().connect() as connection:
        return int(connection.execute(statement).scalar_one())


async def count_account_joins_since(account_id: str, since_iso: str) -> int:
    """Count an account's channel joins since ``since`` — the rolling-window join cap."""
    return await asyncio.to_thread(_count_account_joins_since, account_id, since_iso)
