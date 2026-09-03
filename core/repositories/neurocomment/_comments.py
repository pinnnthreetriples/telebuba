"""Runtime-side neurocomment queries: linked-group cache and comment claims.

Readiness reads/writes live in ``_readiness.py`` (file-size budget).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from sqlalchemy import case, func, select, update
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
    about: str | None,
    join_request: bool | None,
) -> LinkedDiscussionGroup:
    fields = {
        "linked_chat_id": linked_chat_id,
        "comments_enabled": int(comments_enabled),
        "checked_at": _now_iso(),
        "about": about,
        "join_request": join_request,
    }
    refreshed = dict(fields)
    for fact in ("about", "join_request"):
        if fields[fact] is None:
            # Per fact: a caller that did not learn it (onboarding refreshes comments
            # only; a probe whose reply omitted the join gate) must not erase what an
            # earlier probe cached — nulled, it reads as "never learnt" and forced
            # discovery to re-probe the channel on every run.
            del refreshed[fact]
    statement = (
        sqlite_insert(_neurocomment_linked_groups)
        .values(channel=channel, **fields)
        .on_conflict_do_update(
            index_elements=[_neurocomment_linked_groups.c.channel],
            set_=refreshed,
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
    about: str | None = None,
    join_request: bool | None = None,
) -> LinkedDiscussionGroup:
    """Cache (or refresh) a channel's linked discussion-group resolution.

    ``about``/``join_request`` are the probe-time facts discovery's filters read; each one
    a caller did not learn is left ``None``, which keeps whatever the cache holds for it
    (``None`` on a fresh row, which discovery reads as "must probe").
    """
    return await asyncio.to_thread(
        _upsert_linked_group,
        channel,
        linked_chat_id,
        comments_enabled=comments_enabled,
        about=about,
        join_request=join_request,
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
    return _insert_comment(_claim_values(channel, post_id, campaign_id, account_id, status))


def _claim_values(
    channel: str,
    post_id: int,
    campaign_id: str,
    account_id: str,
    status: CommentStatus,
) -> dict[str, object]:
    now = _now_iso()
    return {
        "channel": channel,
        "post_id": post_id,
        "campaign_id": campaign_id,
        "account_id": account_id,
        "status": status,
        "comment_text": None,
        "comment_msg_id": None,
        "created_at": now,
        "updated_at": now,
    }


def _insert_comment(values: dict[str, object]) -> bool:
    statement = (
        sqlite_insert(_neurocomment_comments)
        .values(**values)
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
    if status in ("posted", "failed"):
        is_reply = _neurocomment_comments.c.reply_state.is_not(None)
        values["reply_state"] = case(
            (is_reply, "terminal"),
            else_=_neurocomment_comments.c.reply_state,
        )
        values["reply_outcome"] = case(
            (
                is_reply,
                case(
                    (
                        _neurocomment_comments.c.reply_stage.in_(("dispatching", "dispatched")),
                        "ambiguous" if status == "failed" else "terminal",
                    ),
                    else_="terminal",
                ),
            ),
            else_=_neurocomment_comments.c.reply_outcome,
        )
        if status == "posted":
            values["reply_stage"] = case(
                (is_reply, "dispatched"),
                else_=_neurocomment_comments.c.reply_stage,
            )
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
            .values(
                comment_msg_id=comment_msg_id,
                reply_stage=case(
                    (_neurocomment_comments.c.reply_state.is_not(None), "dispatched"),
                    else_=_neurocomment_comments.c.reply_stage,
                ),
                updated_at=_now_iso(),
            ),
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


# Compatibility facade: callers and package exports historically import these here.
from core.repositories.neurocomment._comment_lifecycle import (  # noqa: E402, F401
    reclaim_stale_claims,
    release_claim,
    touch_comment_claim,
)


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
