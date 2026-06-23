"""Neurocomment comment-quota reads — per-account and bulk grouped claimed+posted counts.

Split out of ``_comments`` to keep each repository module within the file-size budget.
``core.db`` re-exports these via the package ``__init__``, so call sites are unchanged.

Counting ``claimed`` as well as ``posted`` makes an in-flight claim consume quota
immediately, so a burst arriving inside the reply-delay window can't stack past the cap.
Public functions wrap sync helpers via ``asyncio.to_thread`` and return ints / Pydantic
models — never raw rows (non-negotiable #2).
"""

from __future__ import annotations

import asyncio

from sqlalchemy import func, select

from core.db import _get_engine
from core.repositories.neurocomment._tables import _neurocomment_comments
from schemas.neurocomment import AccountCommentCount, CommentCountList


def _count_account_comments_since(account_id: str, since_iso: str) -> int:
    statement = select(func.count()).where(
        (_neurocomment_comments.c.account_id == account_id)
        & (_neurocomment_comments.c.status.in_(("claimed", "posted")))
        & (_neurocomment_comments.c.created_at >= since_iso),
    )
    with _get_engine().connect() as connection:
        return int(connection.execute(statement).scalar_one())


async def count_account_comments_since(account_id: str, since_iso: str) -> int:
    """Count an account's in-flight + delivered (claimed/posted) comments since ``since``."""
    return await asyncio.to_thread(_count_account_comments_since, account_id, since_iso)


def _count_account_channel_comments_since(account_id: str, channel: str, since_iso: str) -> int:
    statement = select(func.count()).where(
        (_neurocomment_comments.c.account_id == account_id)
        & (_neurocomment_comments.c.channel == channel)
        & (_neurocomment_comments.c.status.in_(("claimed", "posted")))
        & (_neurocomment_comments.c.created_at >= since_iso),
    )
    with _get_engine().connect() as connection:
        return int(connection.execute(statement).scalar_one())


async def count_account_channel_comments_since(
    account_id: str,
    channel: str,
    since_iso: str,
) -> int:
    """Count claimed+posted comments for one (account, channel) since ``since`` (day cap)."""
    return await asyncio.to_thread(
        _count_account_channel_comments_since,
        account_id,
        channel,
        since_iso,
    )


def _count_comments_per_account_since(since_iso: str) -> CommentCountList:
    statement = (
        select(_neurocomment_comments.c.account_id, func.count().label("n"))
        .where(
            (_neurocomment_comments.c.status.in_(("claimed", "posted")))
            & (_neurocomment_comments.c.created_at >= since_iso),
        )
        .group_by(_neurocomment_comments.c.account_id)
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).all()
    return CommentCountList(
        counts=[AccountCommentCount(account_id=str(row[0]), count=int(row[1])) for row in rows],
    )


async def count_comments_per_account_since(since_iso: str) -> CommentCountList:
    """Per-account claimed+posted counts since ``since`` — bulk hourly-quota read.

    The grouped equivalent of :func:`count_account_comments_since` for every account
    at once, so selection scores N candidates from one query instead of N.
    """
    return await asyncio.to_thread(_count_comments_per_account_since, since_iso)


def _count_channel_comments_per_account_since(channel: str, since_iso: str) -> CommentCountList:
    statement = (
        select(_neurocomment_comments.c.account_id, func.count().label("n"))
        .where(
            (_neurocomment_comments.c.channel == channel)
            & (_neurocomment_comments.c.status.in_(("claimed", "posted")))
            & (_neurocomment_comments.c.created_at >= since_iso),
        )
        .group_by(_neurocomment_comments.c.account_id)
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).all()
    return CommentCountList(
        counts=[AccountCommentCount(account_id=str(row[0]), count=int(row[1])) for row in rows],
    )


async def count_channel_comments_per_account_since(
    channel: str,
    since_iso: str,
) -> CommentCountList:
    """Per-account claimed+posted counts for one channel since ``since`` — bulk day-cap read."""
    return await asyncio.to_thread(_count_channel_comments_per_account_since, channel, since_iso)
