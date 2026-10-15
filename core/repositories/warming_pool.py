"""Channels a search found, written straight into the walked pool (Прогрев 2.0 follow-up).

A search — «Найти каналы», the automatic refill, or a warming account looking for
itself — adds what passed its filters as ``approved``; nobody approves them by hand any
more. A channel already in the pool, in any status, is left as it is: that is what
keeps a channel the operator removed (``status='removed'``) from coming back.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from sqlalchemy import func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core.db import _get_engine, _now_iso, _warming_channels

if TYPE_CHECKING:
    from collections.abc import Sequence

    from schemas.warming import WarmingChannel

_APPROVED = "approved"
_REMOVED = "removed"


def _known_channels() -> set[str]:
    with _get_engine().connect() as connection:
        return set(connection.execute(select(_warming_channels.c.channel)).scalars())


async def list_known_warming_channels() -> set[str]:
    """Every channel the pool has a row for — walked or removed by the operator."""
    return await asyncio.to_thread(_known_channels)


def _add_found(channels: Sequence[WarmingChannel], limit: int) -> list[str]:
    table = _warming_channels
    added: list[str] = []
    with _get_engine().begin() as connection:
        walked = connection.execute(
            select(func.count()).select_from(table).where(table.c.status == _APPROVED)
        ).scalar_one()
        for channel in channels:
            if walked + len(added) >= limit:
                break
            statement = (
                sqlite_insert(table)
                .values(
                    channel=channel.channel,
                    label=channel.label,
                    created_at=_now_iso(),
                    language=channel.language,
                    country=channel.country,
                    kind=channel.kind,
                    subscribers=channel.subscribers,
                    last_post_at=channel.last_post_at,
                    source=channel.source,
                    status=_APPROVED,
                    found_by=channel.found_by,
                )
                .on_conflict_do_nothing(index_elements=[table.c.channel])
            )
            if connection.execute(statement).rowcount:
                added.append(channel.channel)
    return added


async def add_found_warming_channels(channels: Sequence[WarmingChannel], limit: int) -> list[str]:
    """Insert the found channels as approved, up to ``limit`` walked rows in all.

    Returns the ones that were new to the pool, in order. The count and the inserts
    share one transaction, so two searches finishing together cannot both fill the
    last free slot.
    """
    return await asyncio.to_thread(_add_found, channels, limit)


def _restore(channel: str) -> bool:
    table = _warming_channels
    statement = (
        update(table)
        .where(table.c.channel == channel, table.c.status == _REMOVED)
        .values(status=_APPROVED, source="manual", found_by=None)
    )
    with _get_engine().begin() as connection:
        return connection.execute(statement).rowcount > 0


async def restore_removed_warming_channel(channel: str) -> bool:
    """The operator typed a channel they had removed: walk it again, as their own pick."""
    return await asyncio.to_thread(_restore, channel)
