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
    _neurocomment_campaigns,
)
from schemas.neurocomment import CampaignChannelLink, CampaignChannelList


def _stamp_channel_post_seen(channel: str, seen_at: str) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            update(_neurocomment_campaign_channels)
            .where(
                _campaign_channel_matches(channel)
                & (_neurocomment_campaign_channels.c.active == 1)
                & (
                    _neurocomment_campaign_channels.c.last_post_at.is_(None)
                    | (_neurocomment_campaign_channels.c.last_post_at < seen_at)
                ),
            )
            .values(last_post_at=seen_at),
        )


async def stamp_channel_post_seen(channel: str, seen_at: str) -> None:
    """Record that ``channel`` published at ``seen_at``; no-op if nothing links it.

    One indexed point write per incoming post, on the partial unique index
    ``fetch_active_campaign_for_channel`` already uses (migration #39's case fold).

    Never moves the stamp BACKWARDS — the same "only if there is something to change"
    predicate ``_pauses.clear_channel_pause`` uses, and it costs a predicate rather than a
    read. Two writers race here: a live post stamping ``now``, and the inactive-channel
    rule repairing the stamp from a Telegram read that started before it. Unguarded, the
    repair lands second, overwrites ``now`` with a date days older, and re-nominates a
    demonstrably active channel on the next tick — spending another Telegram read to learn
    the same thing.
    """
    await asyncio.to_thread(_stamp_channel_post_seen, channel, seen_at)


def _list_silent_watch_channels(cutoff: str, limit: int) -> CampaignChannelList:
    statement = (
        select(_neurocomment_campaign_channels)
        .select_from(
            _neurocomment_campaign_channels.join(
                _neurocomment_campaigns,
                _neurocomment_campaign_channels.c.campaign_id
                == _neurocomment_campaigns.c.campaign_id,
            ),
        )
        .where(
            (_neurocomment_campaign_channels.c.active == 1)
            & (_neurocomment_campaigns.c.status == "active")
            & (
                func.coalesce(
                    _neurocomment_campaign_channels.c.last_post_at,
                    _neurocomment_campaign_channels.c.created_at,
                )
                <= cutoff
            ),
        )
        .order_by(_neurocomment_campaign_channels.c.id.asc())
        .limit(limit)
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return CampaignChannelList(
        links=[CampaignChannelLink.model_validate(dict(row)) for row in rows],
    )


async def list_silent_watch_channels(cutoff: str, limit: int) -> CampaignChannelList:
    """At most ``limit`` active links we have not seen publish since ``cutoff``.

    Suspects, not verdicts: our silence is also what downtime and a broken subscription
    look like, so the caller confirms each one against Telegram before acting.

    Joined to the campaign and filtered on ``status == "active"``, exactly as the listener's
    own watch set is: a paused campaign is unsubscribed, so NOTHING can stamp its channels
    and every one of them ages past any cutoff by construction. Without the join, pausing a
    campaign for a fortnight would hand its channels to a rule that unlinks them.

    The ``coalesce`` is why a freshly linked channel is not instantly a suspect — with no
    post seen yet, the link's own age is the honest measure, and it also means the rule
    needs no backfill for the rows that predate migration #51.

    ``limit`` bounds the caller's Telegram reads, and is why the order is by ``id``: on the
    first tick after the upgrade EVERY link is a suspect (the migration leaves the column
    NULL on purpose), so an unbounded list would fire one read per channel in one burst on
    the single listener account. Oldest link first, and the rest come round on later ticks.
    """
    return await asyncio.to_thread(_list_silent_watch_channels, cutoff, limit)
