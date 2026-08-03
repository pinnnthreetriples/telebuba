"""Comment-deletion reads and writes — split from ``_comments`` (file-size budget).

The deletion sweep (and the future live handler) reads the comments that actually
reached a channel and stamps ``deleted_at`` on the ones whose message id has vanished
from it. Kept in its own module so ``_comments`` stays within the aislop size cap;
re-exported by the package so ``core.db`` reaches these unchanged.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select, update

from core.db import _get_engine, _now_iso
from core.repositories.neurocomment._comments import _row_to_comment
from core.repositories.neurocomment._tables import _neurocomment_comments
from schemas.neurocomment import CommentList


def _list_delivered_comments_since(campaign_id: str, since_iso: str) -> CommentList:
    statement = select(_neurocomment_comments).where(
        (_neurocomment_comments.c.campaign_id == campaign_id)
        # Whatever the STATUS says: a row carrying a message id is a comment Telegram
        # confirmed, and the sweep is the only thing watching whether it is still there.
        # Filtering on ``posted`` instead left every mis-classified comment (a claim
        # reclaimed mid-send, a crash between the send and the commit) permanently
        # unwatched — live under the post, unable to put its channel into back-off.
        & _neurocomment_comments.c.comment_msg_id.is_not(None)
        & (_neurocomment_comments.c.created_at >= since_iso),
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return CommentList(comments=[_row_to_comment(row) for row in rows])


async def list_delivered_comments_since(campaign_id: str, since_iso: str) -> CommentList:
    """A campaign's comments that reached Telegram since ``since`` — the sweep's scan set.

    Not ``list_posted_comments_since``: that one answers "what did this campaign
    achieve" (the board's counters) and must keep counting only ``posted``.
    """
    return await asyncio.to_thread(_list_delivered_comments_since, campaign_id, since_iso)


def _mark_comments_deleted(channel: str, comment_msg_ids: list[int]) -> CommentList:
    if not comment_msg_ids:
        return CommentList()
    now = _now_iso()
    with _get_engine().begin() as connection:
        # Only stamp still-live rows, so re-noticing the same deletion (the sweep
        # re-reads the same window for hours) never re-marks or double-logs. The status
        # predicate is loosened rather than dropped: carrying one of these ids IS delivery,
        # so a row mis-classified ``failed`` must still be markable (see the reader above) —
        # but a ``claimed`` row is a claim someone may still be holding, and this write
        # bumps ``updated_at``, which would defer the stale-claim reclaim another cutoff and
        # leave the nonsense state ``claimed`` WITH ``deleted_at`` that nothing prunes.
        connection.execute(
            update(_neurocomment_comments)
            .where(
                (_neurocomment_comments.c.channel == channel)
                & (_neurocomment_comments.c.status != "claimed")
                & _neurocomment_comments.c.deleted_at.is_(None)
                & _neurocomment_comments.c.comment_msg_id.in_(comment_msg_ids),
            )
            .values(deleted_at=now, updated_at=now),
        )
        rows = (
            connection.execute(
                select(_neurocomment_comments).where(
                    (_neurocomment_comments.c.channel == channel)
                    & (_neurocomment_comments.c.deleted_at == now),
                ),
            )
            .mappings()
            .all()
        )
    return CommentList(comments=[_row_to_comment(row) for row in rows])


async def mark_comments_deleted(channel: str, comment_msg_ids: list[int]) -> CommentList:
    """Stamp ``deleted_at`` on this channel's delivered comments whose msg-id vanished.

    Returns only the rows newly marked this call (idempotent across repeated sweeps),
    so the caller can log/announce exactly the fresh deletions.
    """
    return await asyncio.to_thread(_mark_comments_deleted, channel, comment_msg_ids)
