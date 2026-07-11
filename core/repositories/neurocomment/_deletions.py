"""Comment-deletion writes — split from ``_comments`` (file-size budget).

The deletion sweep (and the future live handler) stamps ``deleted_at`` on posted
comments whose message id has vanished from the channel. Kept in its own module so
``_comments`` stays within the aislop size cap; re-exported by the package so
``core.db`` reaches ``mark_comments_deleted`` unchanged.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select, update

from core.db import _get_engine, _now_iso
from core.repositories.neurocomment._comments import _row_to_comment
from core.repositories.neurocomment._tables import _neurocomment_comments
from schemas.neurocomment import CommentList


def _mark_comments_deleted(channel: str, comment_msg_ids: list[int]) -> CommentList:
    if not comment_msg_ids:
        return CommentList()
    now = _now_iso()
    with _get_engine().begin() as connection:
        # Only stamp posted-and-still-live rows, so re-noticing the same deletion
        # (the sweep re-reads the same window for hours) never re-marks or double-logs.
        connection.execute(
            update(_neurocomment_comments)
            .where(
                (_neurocomment_comments.c.channel == channel)
                & (_neurocomment_comments.c.status == "posted")
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
    """Stamp ``deleted_at`` on this channel's posted comments whose msg-id vanished.

    Returns only the rows newly marked this call (idempotent across repeated sweeps),
    so the caller can log/announce exactly the fresh deletions.
    """
    return await asyncio.to_thread(_mark_comments_deleted, channel, comment_msg_ids)
