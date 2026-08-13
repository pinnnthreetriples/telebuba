"""Neurocomment comment-quota reads — per-account and bulk grouped pending+delivered counts.

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

# ``waiting`` spends the slot exactly like ``claimed`` does, and for a sharper reason: a
# parked post has no worker yet, so it looks free while it sits — but every one of them
# WILL send the moment the sweep promotes it. Left uncounted, ten posts parked inside one
# hour all read as costing nothing and then fire together straight through the hourly cap.
_QUOTA_SPENDING_STATUSES = ("waiting", "claimed", "posted")


def _count_account_comments_since(account_id: str, since_iso: str) -> int:
    statement = select(func.count()).where(
        (_neurocomment_comments.c.account_id == account_id)
        & (_neurocomment_comments.c.status.in_(_QUOTA_SPENDING_STATUSES))
        & (_neurocomment_comments.c.created_at >= since_iso),
    )
    with _get_engine().connect() as connection:
        return int(connection.execute(statement).scalar_one())


async def count_account_comments_since(account_id: str, since_iso: str) -> int:
    """Count an account's parked + in-flight + delivered comments since ``since``."""
    return await asyncio.to_thread(_count_account_comments_since, account_id, since_iso)


def _count_account_channel_comments_since(account_id: str, channel: str, since_iso: str) -> int:
    statement = select(func.count()).where(
        (_neurocomment_comments.c.account_id == account_id)
        & (_neurocomment_comments.c.channel == channel)
        & (_neurocomment_comments.c.status.in_(_QUOTA_SPENDING_STATUSES))
        & (_neurocomment_comments.c.created_at >= since_iso),
    )
    with _get_engine().connect() as connection:
        return int(connection.execute(statement).scalar_one())


async def count_account_channel_comments_since(
    account_id: str,
    channel: str,
    since_iso: str,
) -> int:
    """Count quota-spending comments for one (account, channel) since ``since`` (day cap)."""
    return await asyncio.to_thread(
        _count_account_channel_comments_since,
        account_id,
        channel,
        since_iso,
    )


def _count_comments_per_account_since(
    account_ids: list[str],
    since_iso: str,
) -> CommentCountList:
    if not account_ids:
        return CommentCountList()
    # The account filter is what makes this a SEARCH: ix_nc_comments_account_status_created
    # is account-leading, so without it SQLite can only walk the whole index (verified via
    # EXPLAIN QUERY PLAN — "SCAN … USING COVERING INDEX ix_nc_comments_account_status_created"
    # before, "SEARCH … (account_id=? AND status=? AND created_at>?)" after).
    statement = (
        select(_neurocomment_comments.c.account_id, func.count().label("n"))
        .where(
            _neurocomment_comments.c.account_id.in_(account_ids)
            & (_neurocomment_comments.c.status.in_(_QUOTA_SPENDING_STATUSES))
            & (_neurocomment_comments.c.created_at >= since_iso),
        )
        .group_by(_neurocomment_comments.c.account_id)
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).all()
    return CommentCountList(
        counts=[AccountCommentCount(account_id=str(row[0]), count=int(row[1])) for row in rows],
    )


async def count_comments_per_account_since(
    account_ids: list[str],
    since_iso: str,
) -> CommentCountList:
    """Per-account quota-spending counts since ``since`` — bulk hourly-quota read.

    The grouped equivalent of :func:`count_account_comments_since` for the given
    candidates, so selection scores N candidates from one query instead of N. Scoped
    to ``account_ids`` because the cost is otherwise O(all comments ever written) —
    an unbounded per-post scan — not O(candidates).
    """
    return await asyncio.to_thread(_count_comments_per_account_since, account_ids, since_iso)


def _count_channel_comments_per_account_since(
    channel: str,
    account_ids: list[str],
    since_iso: str,
) -> CommentCountList:
    if not account_ids:
        return CommentCountList()
    statement = (
        select(_neurocomment_comments.c.account_id, func.count().label("n"))
        .where(
            (_neurocomment_comments.c.channel == channel)
            & _neurocomment_comments.c.account_id.in_(account_ids)
            & (_neurocomment_comments.c.status.in_(_QUOTA_SPENDING_STATUSES))
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
    account_ids: list[str],
    since_iso: str,
) -> CommentCountList:
    """Per-account quota-spending counts for one channel since ``since`` — bulk day-cap read.

    Channel-leading (ix_nc_comments_channel_account_status_created) so it already
    SEARCHes; the account scope mirrors :func:`count_comments_per_account_since` so both
    bulk quota readers cost O(candidates) and neither can be called fleet-wide by mistake.
    """
    return await asyncio.to_thread(
        _count_channel_comments_per_account_since,
        channel,
        account_ids,
        since_iso,
    )
