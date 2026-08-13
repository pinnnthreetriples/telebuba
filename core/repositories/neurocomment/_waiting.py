"""Parked-post reads/writes for the reply-to-human-comments mode.

Split off ``_comments`` the way ``_quota`` was, to keep both inside the file-size budget;
``core.db`` re-exports these via the package ``__init__``, so call sites are unchanged.

A parked post is one whose claim is won but deliberately not spent yet: we wait up to N
minutes for humans to comment under it, then reply to one of them. The wait is owned by
the five-minute sweep rather than a live task, so a restart cannot lose it — which is also
why every function here has to survive being called twice for the same post.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select, update

from core.db import _get_engine, _now_iso
from core.repositories.neurocomment._comments import _claim_comment, _row_to_comment
from core.repositories.neurocomment._tables import _neurocomment_comments
from schemas.neurocomment import CommentList


async def park_comment(channel: str, post_id: int, campaign_id: str, account_id: str) -> bool:
    """Reserve ``(channel, post_id)`` as ``waiting`` instead of commenting now; ``True`` if won.

    Deliberately the SAME single conditional INSERT as :func:`claim_comment`, only the
    status differs, because that INSERT *is* the double-comment guard: a re-delivered
    listener update or a restart replaying the post loses the conflict and gets ``False``,
    whether the winner is sending now or parked. A second write path here would have to
    re-earn that guarantee, and any read-then-insert version would not have it at all.

    No deadline column is needed: the INSERT stamps ``created_at``, so the wait expires at
    ``created_at + N`` with N read from settings — nothing extra to persist, and a settings
    change re-times the posts already parked instead of leaving them on a frozen deadline.
    """
    return await asyncio.to_thread(
        _claim_comment,
        channel,
        post_id,
        campaign_id,
        account_id,
        "waiting",
    )


def _list_waiting_comments() -> CommentList:
    statement = select(_neurocomment_comments).where(
        _neurocomment_comments.c.status == "waiting",
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return CommentList(comments=[_row_to_comment(row) for row in rows])


async def list_waiting_comments() -> CommentList:
    """Every parked post, fleet-wide — the sweep's whole work list.

    Unscoped by channel or campaign on purpose: nothing else revisits these rows, so a
    reader that saw only some channels would leave the rest parked forever. Each row
    carries its own ``created_at``, which is all the caller needs to decide who is due.
    """
    return await asyncio.to_thread(_list_waiting_comments)


def _promote_waiting_to_claimed(channel: str, post_id: int) -> bool:
    with _get_engine().begin() as connection:
        result = connection.execute(
            update(_neurocomment_comments)
            .where(
                (_neurocomment_comments.c.channel == channel)
                & (_neurocomment_comments.c.post_id == post_id)
                & (_neurocomment_comments.c.status == "waiting"),
            )
            .values(status="claimed", updated_at=_now_iso()),
        )
    return result.rowcount > 0


async def promote_waiting_to_claimed(channel: str, post_id: int) -> bool:
    """Take a parked post out of the wait into an in-flight claim; ``True`` if it is ours.

    The other half of the guard :func:`park_comment` opens. Parking wins the post once, but
    the wait then outlives the process, so the row gets read again and again: two sweep
    ticks overlapping, or a tick racing the startup sweep, both see the same ``waiting`` row
    and without this only-one-wins UPDATE both would reply under one post. ``False`` means
    somebody else already took it — the caller must send nothing.

    ``claimed`` and not straight to the send because that is what puts the row back under
    ``reclaim_stale_claims``, and the ``updated_at`` bump starts that clock here: the
    fifteen-minute backstop then measures the send, never the ten-minute wait.
    """
    return await asyncio.to_thread(_promote_waiting_to_claimed, channel, post_id)
