"""When each watched channel last published, persisted on the campaign link (migration #51).

Its own module because ``_campaigns`` is at the file-size cap, and because this is a
distinct concern: ``_campaigns`` owns which channel belongs to which campaign, while these
two functions own the one fact the inactive-channel rule ages. Same conventions as
``_pauses``: always the migration #39 case fold, always the ACTIVE link — an inactive one
is invisible to every reader, so writing to it would be a silent no-op.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import func, select, update

from core.db import _get_engine
from core.repositories.neurocomment._tables import (
    _campaign_channel_matches,
    _neurocomment_campaign_channels,
)
from schemas.neurocomment import CampaignChannelLink, CampaignChannelList


def _stamp_channel_post_seen(channel: str, seen_at: str) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            update(_neurocomment_campaign_channels)
            .where(
                _campaign_channel_matches(channel)
                & (_neurocomment_campaign_channels.c.active == 1),
            )
            .values(last_post_at=seen_at),
        )


async def stamp_channel_post_seen(channel: str, seen_at: str) -> None:
    """Record that ``channel`` published at ``seen_at``; no-op if nothing links it.

    One indexed point write per incoming post — a few dozen a day across the fleet, on the
    partial unique index ``fetch_active_campaign_for_channel`` already uses. Unconditional
    rather than "only if newer": posts arrive in order and a stale write would cost the
    rule a week, while the guard would cost a read on every post.
    """
    await asyncio.to_thread(_stamp_channel_post_seen, channel, seen_at)


def _list_silent_watch_channels(cutoff: str) -> CampaignChannelList:
    statement = select(_neurocomment_campaign_channels).where(
        (_neurocomment_campaign_channels.c.active == 1)
        & (
            func.coalesce(
                _neurocomment_campaign_channels.c.last_post_at,
                _neurocomment_campaign_channels.c.created_at,
            )
            <= cutoff
        ),
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return CampaignChannelList(
        links=[CampaignChannelLink.model_validate(dict(row)) for row in rows],
    )


async def list_silent_watch_channels(cutoff: str) -> CampaignChannelList:
    """Every active link we have not seen publish since ``cutoff``.

    Suspects, not verdicts: our silence is also what downtime and a broken subscription
    look like, so the caller confirms each one against Telegram before acting. The
    ``coalesce`` is why a freshly linked channel is not instantly a suspect — with no post
    seen yet, the link's own age is the honest measure, and it also means the rule needs no
    backfill for the rows that predate migration #51.
    """
    return await asyncio.to_thread(_list_silent_watch_channels, cutoff)
