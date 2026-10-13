"""Channel-pool proposals awaiting the operator — ``warming_channels.status='pending'``.

A pool-fill search (Прогрев 2.0, item 4.5) proposes channels; none of them is walked
until the operator approves it (decision Q12: manual approval only). Proposals are
inserted beside the approved rows, never over them: a channel already in the pool,
pending or approved, is left as it is.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core.db import _get_engine, _now_iso, _warming_channels
from core.repositories.warming import _row_to_warming_channel
from schemas.warming import WarmingChannelList

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from schemas.warming import WarmingChannel

_PENDING = "pending"


def _list_pending() -> WarmingChannelList:
    table = _warming_channels
    statement = select(table).where(table.c.status == _PENDING).order_by(table.c.id.asc())
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return WarmingChannelList(
        channels=[_row_to_warming_channel(cast("Mapping[str, object]", row)) for row in rows],
    )


async def list_pending_warming_channels() -> WarmingChannelList:
    """Every proposal still waiting for the operator, oldest first."""
    return await asyncio.to_thread(_list_pending)


def _propose(channels: Sequence[WarmingChannel]) -> int:
    added = 0
    with _get_engine().begin() as connection:
        for channel in channels:
            statement = (
                sqlite_insert(_warming_channels)
                .values(
                    channel=channel.channel,
                    label=channel.label,
                    created_at=_now_iso(),
                    language=channel.language,
                    country=channel.country,
                    kind=channel.kind,
                    subscribers=channel.subscribers,
                    last_post_at=channel.last_post_at,
                    source="discovery",
                    status=_PENDING,
                )
                .on_conflict_do_nothing(index_elements=[_warming_channels.c.channel])
            )
            added += connection.execute(statement).rowcount
    return added


async def propose_warming_channels(channels: Sequence[WarmingChannel]) -> int:
    """Insert proposals as pending; returns how many were new to the pool."""
    return await asyncio.to_thread(_propose, channels)


def _approve(channels: Sequence[str]) -> int:
    table = _warming_channels
    statement = (
        update(table)
        .where(table.c.channel.in_(list(channels)), table.c.status == _PENDING)
        .values(status="approved")
    )
    with _get_engine().begin() as connection:
        return connection.execute(statement).rowcount


async def approve_warming_channels(channels: Sequence[str]) -> int:
    """Move these proposals into the walked pool; returns how many were pending."""
    return await asyncio.to_thread(_approve, channels)


def _reject(channels: Sequence[str]) -> int:
    table = _warming_channels
    statement = delete(table).where(table.c.channel.in_(list(channels)), table.c.status == _PENDING)
    with _get_engine().begin() as connection:
        return connection.execute(statement).rowcount


async def reject_warming_channels(channels: Sequence[str]) -> int:
    """Drop these proposals; an approved channel is never removed this way."""
    return await asyncio.to_thread(_reject, channels)
