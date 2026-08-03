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

from sqlalchemy import func, select, update

from core.db import _get_engine
from core.repositories.neurocomment._tables import _neurocomment_join_log


def _record_join(account_id: str, watch_channel: str | None) -> None:
    statement = _neurocomment_join_log.insert().values(
        account_id=account_id,
        joined_at=datetime.now(UTC).isoformat(),
        watch_channel=watch_channel,
    )
    with _get_engine().begin() as connection:
        connection.execute(statement)


async def record_join(account_id: str, watch_channel: str | None = None) -> None:
    """Stamp one successful channel join for ``account_id`` (now, UTC isoformat).

    ``watch_channel`` is set by the listener pass only; a discussion-group join leaves
    it ``None`` (readiness tracks that pair) so it can never make the listener skip the
    broadcast channel it must receive posts from.
    """
    await asyncio.to_thread(_record_join, account_id, watch_channel)


def _list_joined_watch_channels(account_id: str) -> set[str]:
    statement = select(_neurocomment_join_log.c.watch_channel).where(
        (_neurocomment_join_log.c.account_id == account_id)
        & _neurocomment_join_log.c.watch_channel.is_not(None)
        # A join Telegram has since disproven is not a membership, so it must not seed the
        # pass's skip cache — but the row stays, because the cap above still counts it.
        & _neurocomment_join_log.c.lost_at.is_(None),
    )
    with _get_engine().connect() as connection:
        return {str(row[0]) for row in connection.execute(statement)}


async def list_joined_watch_channels(account_id: str) -> set[str]:
    """Watch channels ``account_id`` is still inside — the restart-safe join cache."""
    return await asyncio.to_thread(_list_joined_watch_channels, account_id)


def _mark_watch_channel_join_lost(account_id: str, watch_channel: str) -> int | None:
    pair = (
        (_neurocomment_join_log.c.account_id == account_id)
        # Never widened to a whole class of rows: ``watch_channel=None`` would render
        # ``IS NULL`` and swallow every discussion-group join this account ever made.
        & _neurocomment_join_log.c.watch_channel.is_not(None)
        & (_neurocomment_join_log.c.watch_channel == watch_channel)
    )
    with _get_engine().begin() as connection:
        stamped = connection.execute(
            update(_neurocomment_join_log)
            .where(pair & _neurocomment_join_log.c.lost_at.is_(None))
            .values(lost_at=datetime.now(UTC).isoformat()),
        ).rowcount
        if not stamped:
            return None
        return int(
            connection.execute(
                select(func.count()).where(pair & _neurocomment_join_log.c.lost_at.is_not(None)),
            ).scalar_one(),
        )


async def mark_watch_channel_join_lost(account_id: str, watch_channel: str) -> int | None:
    """Stamp the standing join of ``watch_channel`` as disproven; return attempts spent.

    Callable only when Telegram has PROVEN the account is out (kicked / banned / the
    channel went private). The row is stamped, never deleted: deleting it made the
    rolling-24h cap unreachable — one row out, one row in per pass, so the count stayed
    flat and the brake meant to bound the re-join loop could never engage.

    Returns the number of joins of this pair now known lost, which IS the re-join attempt
    count the caller bounds, or ``None`` when there was no standing join to disprove
    (already stamped this loss, or the join never landed a row) — nothing new to report.
    """
    return await asyncio.to_thread(_mark_watch_channel_join_lost, account_id, watch_channel)


def _list_exhausted_watch_channels(account_id: str, max_attempts: int) -> set[str]:
    statement = (
        select(_neurocomment_join_log.c.watch_channel)
        .where(
            (_neurocomment_join_log.c.account_id == account_id)
            & _neurocomment_join_log.c.watch_channel.is_not(None)
            & _neurocomment_join_log.c.lost_at.is_not(None),
        )
        .group_by(_neurocomment_join_log.c.watch_channel)
        .having(func.count() >= max_attempts)
    )
    with _get_engine().connect() as connection:
        return {str(row[0]) for row in connection.execute(statement)}


async def list_exhausted_watch_channels(account_id: str, max_attempts: int) -> set[str]:
    """Watch channels this account has already spent ``max_attempts`` joins losing.

    Persisted rather than remembered, deliberately: the events that re-arm a re-join are
    boot, Start and every channel link, so an in-memory counter would reset before it ever
    bounded anything (the lesson of #147's module dicts).
    """
    return await asyncio.to_thread(_list_exhausted_watch_channels, account_id, max_attempts)


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
