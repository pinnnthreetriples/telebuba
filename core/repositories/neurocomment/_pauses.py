"""The "this channel will not let us write" pause, persisted on the campaign link (#147).

Its own module because ``_campaigns`` is at the file-size cap, and because this is a
distinct concern: ``_campaigns`` owns which channel belongs to which campaign, while
these three functions own how long a channel stays parked and how many rounds it has
left. Both operate on ``neurocomment_campaign_channels``, always through the migration
#39 case fold and always on the ACTIVE link — an inactive one is invisible to every
reader, so writing to it would be a silent no-op.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from sqlalchemy import select, update

from core.db import _get_engine
from core.repositories.neurocomment._tables import (
    _campaign_channel_matches,
    _neurocomment_campaign_channels,
)
from schemas.neurocomment import ChannelPauseState

if TYPE_CHECKING:
    from sqlalchemy import ColumnElement


def _active_link(channel: str) -> ColumnElement[bool]:
    """The active link for ``channel``, matched through the case fold (migration #39)."""
    return _campaign_channel_matches(channel) & (_neurocomment_campaign_channels.c.active == 1)


def _fetch_channel_paused_until(channel: str) -> str | None:
    statement = select(_neurocomment_campaign_channels.c.paused_until).where(_active_link(channel))
    with _get_engine().connect() as connection:
        return connection.execute(statement).scalars().first()


async def fetch_channel_paused_until(channel: str) -> str | None:
    """When ``channel``'s current pause ends, or ``None`` if it is not paused.

    The persisted replacement for the in-memory challenge back-off. One point read on the
    partial unique index that already serves ``fetch_active_campaign_for_channel``, which
    is why the engine can afford it once per post; the deadline has to survive restarts,
    so it cannot be answered from memory. A channel with no active link reads as
    not-paused — nothing posts there anyway.
    """
    return await asyncio.to_thread(_fetch_channel_paused_until, channel)


def _bump_channel_pause(channel: str, until: str) -> ChannelPauseState | None:
    with _get_engine().begin() as connection:
        row = connection.execute(
            select(
                _neurocomment_campaign_channels.c.campaign_id,
                _neurocomment_campaign_channels.c.pause_rounds,
            ).where(_active_link(channel)),
        ).first()
        if row is None:
            return None
        campaign_id, rounds = str(row[0]), int(row[1]) + 1
        connection.execute(
            update(_neurocomment_campaign_channels)
            .where(_active_link(channel))
            .values(pause_rounds=rounds, paused_until=until),
        )
    return ChannelPauseState(campaign_id=campaign_id, pause_rounds=rounds, paused_until=until)


async def bump_channel_pause(channel: str, until: str) -> ChannelPauseState | None:
    """End a round on ``channel``: count it and park the channel until ``until``.

    Pure mechanism — whether the new round count has used up the channel's budget is the
    service's call, since that is the layer allowed to unlink the channel. Read and write
    share one transaction so two concurrent gate failures cannot both write round N.
    ``None`` when the channel has no active link left, which is nothing to pause.
    """
    return await asyncio.to_thread(_bump_channel_pause, channel, until)


def _clear_channel_pause(channel: str) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            update(_neurocomment_campaign_channels)
            .where(
                _active_link(channel)
                & (
                    (_neurocomment_campaign_channels.c.pause_rounds != 0)
                    | _neurocomment_campaign_channels.c.paused_until.is_not(None)
                ),
            )
            .values(pause_rounds=0, paused_until=None),
        )


async def clear_channel_pause(channel: str) -> None:
    """Forget ``channel``'s rounds and pause — it just took a comment, so it does work.

    Runs on every delivered comment, hence the "only if there is something to clear"
    predicate: in the common case it matches no row and SQLite writes nothing.
    """
    await asyncio.to_thread(_clear_channel_pause, channel)
