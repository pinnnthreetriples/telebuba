"""Runtime-side neurocomment queries: linked-group cache and comment claims.

Readiness reads/writes live in ``_readiness.py`` (file-size budget).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select, update
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


def _count_campaign_comments(campaign_id: str) -> int:
    statement = select(func.count()).where(
        _neurocomment_comments.c.campaign_id == campaign_id,
    )
    with _get_engine().connect() as connection:
        return int(connection.execute(statement).scalar_one())


async def count_campaign_comments(campaign_id: str) -> int:
    """Count every comment row a campaign owns, whatever its status.

    Unscoped on purpose, unlike ``_quota``'s counters: the question here is not how much
    quota is spent but how much history hangs off the campaign — the number the delete is
    about to destroy with no way back.
    """
    return await asyncio.to_thread(_count_campaign_comments, campaign_id)


def _claim_comment(
    channel: str,
    post_id: int,
    campaign_id: str,
    account_id: str,
    # Parametrized so ``_waiting.park_comment`` reserves posts through THIS insert
    # instead of a second one that would have to re-earn its conflict guarantee.
    status: CommentStatus = "claimed",
) -> bool:
    now = _now_iso()
    statement = (
        sqlite_insert(_neurocomment_comments)
        .values(
            channel=channel,
            post_id=post_id,
            campaign_id=campaign_id,
            account_id=account_id,
            status=status,
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


def _record_comment_msg_id(channel: str, post_id: int, comment_msg_id: int) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            update(_neurocomment_comments)
            .where(
                (_neurocomment_comments.c.channel == channel)
                & (_neurocomment_comments.c.post_id == post_id),
            )
            .values(comment_msg_id=comment_msg_id, updated_at=_now_iso()),
        )


async def record_comment_msg_id(channel: str, post_id: int, comment_msg_id: int) -> None:
    """Stamp the delivered comment's message id, whatever the row's status says.

    Deliberately NOT guarded by the terminal-status rule ``_mark_comment`` enforces: the
    status is a verdict and can be wrong (a reclaimed claim reads ``failed`` while the
    comment is live under the post), but a message id is a fact Telegram just handed us,
    and it never contradicts a status — it only says which message this row produced.
    Without it a mis-classified comment stays invisible to the deletion sweep forever,
    since that scan can only look at rows carrying an id.
    """
    await asyncio.to_thread(_record_comment_msg_id, channel, post_id, comment_msg_id)


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
    """Drop an in-flight claim for an attempt that provably sent nothing.

    Callable ONLY when the caller knows no comment left the process: an undownloadable
    post image and a Gemini 429 both fail before generation finishes, and a gateway
    ``status="unavailable"`` qualifies only when it is NOT ``UNCONFIRMED_ERROR_TYPE``,
    i.e. when the pool never connected. Those are nobody's fault, so they must not mark
    the row ``failed`` — but leaving it ``claimed`` is not free either: ``_quota`` counts
    ``claimed`` alongside ``posted``, and ``reclaim_stale_claims`` is a backstop that only
    ages a claim out once it is 15 minutes old, so the slot stayed spent long after the
    attempt was over. Deleting the row frees it at once.

    A DELETE rather than a new status because the row records nothing worth keeping — and
    that is also the whole danger: it makes ``(channel, post_id)`` claimable again, so a
    caller that merely HOPES nothing was sent re-opens the double-comment window
    ``claim_comment`` exists to close. Which is exactly what the periodic reclaim can NOT
    infer from age, and why it marks ``failed`` instead of deleting.
    """
    return await asyncio.to_thread(_release_claim, channel, post_id)


def _touch_comment_claim(channel: str, post_id: int) -> bool:
    with _get_engine().begin() as connection:
        result = connection.execute(
            update(_neurocomment_comments)
            .where(
                (_neurocomment_comments.c.channel == channel)
                & (_neurocomment_comments.c.post_id == post_id)
                # Heartbeat an IN-FLIGHT claim only: a terminal row is settled, and
                # bumping its stamp would just hide its real age from the reclaim.
                & (_neurocomment_comments.c.status == "claimed"),
            )
            .values(updated_at=_now_iso()),
        )
    return result.rowcount > 0


async def touch_comment_claim(channel: str, post_id: int) -> bool:
    """Heartbeat a claim the worker is still holding; ``False`` if it is no longer ours.

    ``reclaim_stale_claims`` can only judge a claim by its age, and between winning the
    claim and resolving it the worker wrote nothing at all — so a slow-but-live attempt
    was indistinguishable from a dead one and got failed underneath itself. This is the
    beat that tells them apart.

    The answer matters as much as the write: this used to return ``None`` whether or not it
    matched, so a worker whose claim had been reclaimed out from under it could not tell
    and sent anyway. ``False`` means the row is terminal or gone — the beat had no claim to
    keep alive, and the caller must not send under it.
    """
    return await asyncio.to_thread(_touch_comment_claim, channel, post_id)


def _reclaim_stale_claims(cutoff_iso: str) -> int:
    with _get_engine().begin() as connection:
        result = connection.execute(
            update(_neurocomment_comments)
            .where(
                (_neurocomment_comments.c.status == "claimed")
                # ``updated_at``, not ``created_at``: the claim stamps both, so an
                # un-beaten row ages exactly as it used to, while a worker that beats
                # (``touch_comment_claim``) is no longer failed out from under a send it
                # is still making — the one thing age alone could never see.
                & (_neurocomment_comments.c.updated_at < cutoff_iso),
            )
            .values(status="failed", updated_at=_now_iso()),
        )
    return result.rowcount


async def reclaim_stale_claims(cutoff_iso: str) -> int:
    """Release claims untouched since before cutoff_iso (mark 'failed'); returns count."""
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
