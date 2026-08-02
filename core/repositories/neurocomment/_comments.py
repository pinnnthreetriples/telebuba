"""Runtime-side neurocomment queries: linked-group cache and comment claims.

Readiness reads/writes live in ``_readiness.py`` (file-size budget).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core.db import _get_engine, _now_iso
from core.repositories.neurocomment._tables import (
    _neurocomment_comments,
    _neurocomment_linked_groups,
)
from schemas.neurocomment import (
    CommentList,
    CommentRecord,
    CommentStatus,
    LinkedDiscussionGroup,
    LinkedGroupList,
)

if TYPE_CHECKING:
    from sqlalchemy import RowMapping


def _fetch_linked_group(channel: str) -> LinkedDiscussionGroup | None:
    statement = select(_neurocomment_linked_groups).where(
        _neurocomment_linked_groups.c.channel == channel,
    )
    with _get_engine().connect() as connection:
        row = connection.execute(statement).mappings().first()
    return None if row is None else LinkedDiscussionGroup.model_validate(dict(row))


async def fetch_linked_group(channel: str) -> LinkedDiscussionGroup | None:
    return await asyncio.to_thread(_fetch_linked_group, channel)


def _list_linked_groups(channels: list[str]) -> LinkedGroupList:
    if not channels:
        return LinkedGroupList()
    statement = select(_neurocomment_linked_groups).where(
        _neurocomment_linked_groups.c.channel.in_(channels),
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return LinkedGroupList(
        groups=[LinkedDiscussionGroup.model_validate(dict(row)) for row in rows],
    )


async def list_linked_groups(channels: list[str]) -> LinkedGroupList:
    """Cached linked-group resolutions for a set of channels (bulk read for the board)."""
    return await asyncio.to_thread(_list_linked_groups, channels)


def _upsert_linked_group(
    channel: str,
    linked_chat_id: int | None,
    *,
    comments_enabled: bool,
) -> LinkedDiscussionGroup:
    fields = {
        "linked_chat_id": linked_chat_id,
        "comments_enabled": int(comments_enabled),
        "checked_at": _now_iso(),
    }
    statement = (
        sqlite_insert(_neurocomment_linked_groups)
        .values(channel=channel, **fields)
        .on_conflict_do_update(
            index_elements=[_neurocomment_linked_groups.c.channel],
            set_=fields,
        )
    )
    with _get_engine().begin() as connection:
        connection.execute(statement)
    group = _fetch_linked_group(channel)
    if group is None:  # pragma: no cover - upsert above guarantees the row
        msg = f"Linked group was not persisted: {channel!r}"
        raise RuntimeError(msg)
    return group


async def upsert_linked_group(
    channel: str,
    linked_chat_id: int | None,
    *,
    comments_enabled: bool,
) -> LinkedDiscussionGroup:
    """Cache (or refresh) a channel's linked discussion-group resolution."""
    return await asyncio.to_thread(
        _upsert_linked_group,
        channel,
        linked_chat_id,
        comments_enabled=comments_enabled,
    )


def _row_to_comment(row: RowMapping) -> CommentRecord:
    return CommentRecord.model_validate(dict(row))


def _fetch_comment(channel: str, post_id: int) -> CommentRecord | None:
    statement = select(_neurocomment_comments).where(
        (_neurocomment_comments.c.channel == channel)
        & (_neurocomment_comments.c.post_id == post_id),
    )
    with _get_engine().connect() as connection:
        row = connection.execute(statement).mappings().first()
    return None if row is None else _row_to_comment(row)


async def fetch_comment(channel: str, post_id: int) -> CommentRecord | None:
    return await asyncio.to_thread(_fetch_comment, channel, post_id)


def _claim_comment(channel: str, post_id: int, campaign_id: str, account_id: str) -> bool:
    now = _now_iso()
    statement = (
        sqlite_insert(_neurocomment_comments)
        .values(
            channel=channel,
            post_id=post_id,
            campaign_id=campaign_id,
            account_id=account_id,
            status="claimed",
            comment_text=None,
            comment_msg_id=None,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(
            index_elements=[
                _neurocomment_comments.c.channel,
                _neurocomment_comments.c.post_id,
            ],
        )
    )
    with _get_engine().begin() as connection:
        result = connection.execute(statement)
    return result.rowcount > 0


async def claim_comment(channel: str, post_id: int, campaign_id: str, account_id: str) -> bool:
    """Atomically claim ``(channel, post_id)`` for one account. ``True`` if won."""
    return await asyncio.to_thread(_claim_comment, channel, post_id, campaign_id, account_id)


def _mark_comment(
    channel: str,
    post_id: int,
    *,
    status: CommentStatus,
    comment_text: str | None = None,
    comment_msg_id: int | None = None,
) -> CommentRecord | None:
    values: dict[str, object] = {"status": status, "updated_at": _now_iso()}
    if comment_text is not None:
        values["comment_text"] = comment_text
    if comment_msg_id is not None:
        values["comment_msg_id"] = comment_msg_id
    with _get_engine().begin() as connection:
        connection.execute(
            update(_neurocomment_comments)
            .where(
                (_neurocomment_comments.c.channel == channel)
                & (_neurocomment_comments.c.post_id == post_id)
                # Idempotent: never re-transition a claim that already reached a
                # terminal outcome (a late failure can't unposted a posted comment).
                & _neurocomment_comments.c.status.notin_(("posted", "failed")),
            )
            .values(**values),
        )
    return _fetch_comment(channel, post_id)


async def mark_comment_posted(
    channel: str,
    post_id: int,
    *,
    comment_text: str,
    comment_msg_id: int | None,
) -> CommentRecord | None:
    """Mark a claimed comment as posted.

    Idempotent: an already-terminal claim is not re-transitioned, so the returned
    record is the *current* row — ``None`` only when no row exists.
    """
    return await asyncio.to_thread(
        _mark_comment,
        channel,
        post_id,
        status="posted",
        comment_text=comment_text,
        comment_msg_id=comment_msg_id,
    )


async def mark_comment_failed(channel: str, post_id: int) -> CommentRecord | None:
    """Mark a claimed comment as failed.

    Idempotent: an already-terminal claim is not re-transitioned, so the returned
    record is the *current* row — ``None`` only when no row exists.
    """
    return await asyncio.to_thread(_mark_comment, channel, post_id, status="failed")


def _release_claim(channel: str, post_id: int) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            delete(_neurocomment_comments).where(
                (_neurocomment_comments.c.channel == channel)
                & (_neurocomment_comments.c.post_id == post_id)
                # Guarded, not blind: only a still-in-flight claim may be dropped, so a
                # delivered ('posted') or already-terminal ('failed') row is untouchable
                # even if a late caller aims this at the wrong post.
                & (_neurocomment_comments.c.status == "claimed"),
            ),
        )


async def release_claim(channel: str, post_id: int) -> None:
    """Drop an in-flight claim the attempt never charged to the account.

    The transient outcomes (``status="unavailable"``, a Gemini 429) are nobody's fault,
    so they must not mark the row ``failed`` — but leaving it ``claimed`` is not free
    either: ``_quota`` counts ``claimed`` alongside ``posted``, and ``reclaim_stale_claims``
    only runs at process startup, so the slot stayed spent for the whole 24-hour window.
    Deleting the row frees it at once. A DELETE rather than a new status because the row
    records nothing worth keeping: nothing was generated or sent.
    """
    return await asyncio.to_thread(_release_claim, channel, post_id)


def _reclaim_stale_claims(cutoff_iso: str) -> int:
    with _get_engine().begin() as connection:
        result = connection.execute(
            update(_neurocomment_comments)
            .where(
                (_neurocomment_comments.c.status == "claimed")
                & (_neurocomment_comments.c.created_at < cutoff_iso),
            )
            .values(status="failed", updated_at=_now_iso()),
        )
    return result.rowcount


async def reclaim_stale_claims(cutoff_iso: str) -> int:
    """Release claims stuck 'claimed' since before cutoff_iso (mark 'failed'); returns count."""
    return await asyncio.to_thread(_reclaim_stale_claims, cutoff_iso)


def _list_posted_comments_since(campaign_id: str, since_iso: str) -> CommentList:
    statement = select(_neurocomment_comments).where(
        (_neurocomment_comments.c.campaign_id == campaign_id)
        & (_neurocomment_comments.c.status == "posted")
        & (_neurocomment_comments.c.created_at >= since_iso),
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return CommentList(comments=[_row_to_comment(row) for row in rows])


async def list_posted_comments_since(campaign_id: str, since_iso: str) -> CommentList:
    """A campaign's ``posted`` comments with ``created_at >= since`` (bulk read for the board)."""
    return await asyncio.to_thread(_list_posted_comments_since, campaign_id, since_iso)


def _list_posted_comments_page(campaign_id: str, offset: int, limit: int) -> CommentList:
    # Newest-first; created_at is an ISO string so lexical desc == chronological
    # desc, and post_id desc breaks ties within the same second for a stable page.
    statement = (
        select(_neurocomment_comments)
        .where(
            (_neurocomment_comments.c.campaign_id == campaign_id)
            & (_neurocomment_comments.c.status == "posted"),
        )
        .order_by(
            _neurocomment_comments.c.created_at.desc(),
            _neurocomment_comments.c.post_id.desc(),
        )
        .limit(limit)
        .offset(offset)
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return CommentList(comments=[_row_to_comment(row) for row in rows])


async def list_posted_comments_page(campaign_id: str, offset: int, limit: int) -> CommentList:
    """One page of a campaign's ``posted`` comments (newest first) for the history modal."""
    return await asyncio.to_thread(_list_posted_comments_page, campaign_id, offset, limit)


def _list_posted_comments_for_channel_since(
    campaign_id: str,
    channel: str,
    since_iso: str,
) -> CommentList:
    statement = select(_neurocomment_comments).where(
        (_neurocomment_comments.c.campaign_id == campaign_id)
        & (_neurocomment_comments.c.channel == channel)
        & (_neurocomment_comments.c.status == "posted")
        & (_neurocomment_comments.c.created_at >= since_iso),
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return CommentList(comments=[_row_to_comment(row) for row in rows])


async def list_posted_comments_for_channel_since(
    campaign_id: str,
    channel: str,
    since_iso: str,
) -> CommentList:
    """One channel's ``posted`` comments since ``since`` — scoped read for semantic dedup.

    The channel-scoped equivalent of :func:`list_posted_comments_since`: the engine
    only needs the posting channel's recent comments, so filtering in SQL (backed by the
    campaign+channel index) beats loading the whole campaign and filtering in Python.
    """
    return await asyncio.to_thread(
        _list_posted_comments_for_channel_since,
        campaign_id,
        channel,
        since_iso,
    )
