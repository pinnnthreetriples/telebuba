"""Warming joined channels repository.

Tracks which channels an account has successfully joined, so we skip
repeating the JoinChannel call in every cycle. A channel warming itself left
keeps its row with ``left_at`` set: "joined" means joined-and-not-left, and the
cycle uses ``left_at`` to keep the channel out until the re-join cooldown lapses.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core.db import _get_engine, _now_iso, _warming_joined_channels
from schemas._warming_extras import JoinedChannel


def _is_channel_joined(account_id: str, channel: str) -> bool:
    statement = select(_warming_joined_channels).where(
        (_warming_joined_channels.c.account_id == account_id)
        & (_warming_joined_channels.c.channel == channel)
        & (_warming_joined_channels.c.left_at.is_(None)),
    )
    with _get_engine().connect() as connection:
        return connection.execute(statement).first() is not None


async def is_channel_joined(account_id: str, channel: str) -> bool:
    """True if this account has joined this channel and not left it since."""
    return await asyncio.to_thread(_is_channel_joined, account_id, channel)


def _record_channel_joined(account_id: str, channel: str) -> None:
    # Upsert: a re-join after a leave clears ``left_at`` and restarts the "joined N
    # days ago" clock the leave policy reads, so the channel is not left again at once.
    now = _now_iso()
    statement = (
        sqlite_insert(_warming_joined_channels)
        .values(account_id=account_id, channel=channel, created_at=now, left_at=None)
        .on_conflict_do_update(
            index_elements=[
                _warming_joined_channels.c.account_id,
                _warming_joined_channels.c.channel,
            ],
            set_={"created_at": now, "left_at": None},
        )
    )
    with _get_engine().begin() as connection:
        connection.execute(statement)


async def record_channel_joined(account_id: str, channel: str) -> None:
    """Record that the account has joined (or re-joined) this channel."""
    await asyncio.to_thread(_record_channel_joined, account_id, channel)


def _record_channel_left(account_id: str, channel: str) -> None:
    statement = (
        update(_warming_joined_channels)
        .where(
            (_warming_joined_channels.c.account_id == account_id)
            & (_warming_joined_channels.c.channel == channel),
        )
        .values(left_at=_now_iso())
    )
    with _get_engine().begin() as connection:
        connection.execute(statement)


async def record_channel_left(account_id: str, channel: str) -> None:
    """Record that the account left this channel (the row stays, for the cooldown)."""
    await asyncio.to_thread(_record_channel_left, account_id, channel)


def _list_joined_channels(account_id: str) -> list[JoinedChannel]:
    statement = select(
        _warming_joined_channels.c.channel,
        _warming_joined_channels.c.created_at,
        _warming_joined_channels.c.left_at,
    ).where(_warming_joined_channels.c.account_id == account_id)
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [JoinedChannel.model_validate(dict(row)) for row in rows]


async def list_joined_channels(account_id: str) -> list[JoinedChannel]:
    """Every channel this account ever joined through warming, left ones included."""
    return await asyncio.to_thread(_list_joined_channels, account_id)
