"""Warming joined channels repository.

Tracks which channels an account has successfully joined, so we skip
repeating the JoinChannel call in every cycle.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError

from core.db import _get_engine, _now_iso, _warming_joined_channels


def _is_channel_joined(account_id: str, channel: str) -> bool:
    statement = select(_warming_joined_channels).where(
        (_warming_joined_channels.c.account_id == account_id)
        & (_warming_joined_channels.c.channel == channel),
    )
    with _get_engine().connect() as connection:
        return connection.execute(statement).first() is not None


async def is_channel_joined(account_id: str, channel: str) -> bool:
    """True if this account has successfully joined this channel before."""
    return await asyncio.to_thread(_is_channel_joined, account_id, channel)


def _record_channel_joined(account_id: str, channel: str) -> None:
    with _get_engine().begin() as connection, suppress(IntegrityError):
        connection.execute(
            insert(_warming_joined_channels).values(
                account_id=account_id,
                channel=channel,
                created_at=_now_iso(),
            ),
        )


async def record_channel_joined(account_id: str, channel: str) -> None:
    """Record that the account has joined this channel."""
    await asyncio.to_thread(_record_channel_joined, account_id, channel)
