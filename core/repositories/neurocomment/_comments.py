"""Runtime-side neurocomment queries: linked-group cache, readiness, comment claims."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core.db import _get_engine, _now_iso
from core.repositories.neurocomment._tables import (
    _neurocomment_campaign_accounts,
    _neurocomment_campaign_channels,
    _neurocomment_comments,
    _neurocomment_linked_groups,
    _neurocomment_readiness,
)
from schemas.neurocomment import (
    CommentList,
    CommentRecord,
    CommentStatus,
    LinkedDiscussionGroup,
    LinkedGroupList,
    NeurocommentReadiness,
    ReadinessList,
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


def _fetch_readiness(account_id: str, channel: str) -> NeurocommentReadiness | None:
    statement = select(_neurocomment_readiness).where(
        (_neurocomment_readiness.c.account_id == account_id)
        & (_neurocomment_readiness.c.channel == channel),
    )
    with _get_engine().connect() as connection:
        row = connection.execute(statement).mappings().first()
    return None if row is None else NeurocommentReadiness.model_validate(dict(row))


async def fetch_readiness(account_id: str, channel: str) -> NeurocommentReadiness | None:
    return await asyncio.to_thread(_fetch_readiness, account_id, channel)


def _upsert_readiness(
    account_id: str,
    channel: str,
    *,
    joined: bool,
    captcha_passed: bool,
    ready: bool,
) -> NeurocommentReadiness:
    fields = {
        "joined": int(joined),
        "captcha_passed": int(captcha_passed),
        "ready": int(ready),
        "checked_at": _now_iso(),
    }
    statement = (
        sqlite_insert(_neurocomment_readiness)
        .values(account_id=account_id, channel=channel, **fields)
        .on_conflict_do_update(
            index_elements=[
                _neurocomment_readiness.c.account_id,
                _neurocomment_readiness.c.channel,
            ],
            set_=fields,
        )
    )
    with _get_engine().begin() as connection:
        connection.execute(statement)
    record = _fetch_readiness(account_id, channel)
    if record is None:  # pragma: no cover - upsert above guarantees the row
        msg = f"Readiness was not persisted: {account_id!r}/{channel!r}"
        raise RuntimeError(msg)
    return record


async def upsert_readiness(
    account_id: str,
    channel: str,
    *,
    joined: bool,
    captcha_passed: bool,
    ready: bool,
) -> NeurocommentReadiness:
    """Record per-(account, channel) join/captcha/ready state at onboarding.

    Leaves ``human_skipped`` untouched (an operator skip survives a re-onboard).
    """
    return await asyncio.to_thread(
        _upsert_readiness,
        account_id,
        channel,
        joined=joined,
        captcha_passed=captcha_passed,
        ready=ready,
    )


def _mark_human_skipped(account_id: str, channel: str) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            update(_neurocomment_readiness)
            .where(
                (_neurocomment_readiness.c.account_id == account_id)
                & (_neurocomment_readiness.c.channel == channel),
            )
            .values(human_skipped=1, ready=0, checked_at=_now_iso()),
        )


async def mark_human_skipped(account_id: str, channel: str) -> None:
    """Operator skip: the engine never selects this pair (ready=0, human_skipped=1)."""
    await asyncio.to_thread(_mark_human_skipped, account_id, channel)


def _delete_readiness(account_id: str, channel: str) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            delete(_neurocomment_readiness).where(
                (_neurocomment_readiness.c.account_id == account_id)
                & (_neurocomment_readiness.c.channel == channel),
            ),
        )


async def delete_readiness(account_id: str, channel: str) -> None:
    """Erase a pair's readiness so a retry re-onboards from scratch (clears the skip)."""
    await asyncio.to_thread(_delete_readiness, account_id, channel)


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


def _list_campaign_readiness(campaign_id: str) -> ReadinessList:
    # Readiness is per-(account, channel); scope to the campaign's accounts AND its
    # channels so the board reads every pair in one query instead of N per-card
    # fetches. Scoping by channels too keeps an account shared across campaigns from
    # leaking the other campaign's (account, channel) rows onto this card.
    accounts = select(_neurocomment_campaign_accounts.c.account_id).where(
        _neurocomment_campaign_accounts.c.campaign_id == campaign_id,
    )
    channels = select(_neurocomment_campaign_channels.c.channel).where(
        (_neurocomment_campaign_channels.c.campaign_id == campaign_id)
        & (_neurocomment_campaign_channels.c.active == 1),
    )
    statement = select(_neurocomment_readiness).where(
        _neurocomment_readiness.c.account_id.in_(accounts)
        & _neurocomment_readiness.c.channel.in_(channels),
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return ReadinessList(
        readiness=[NeurocommentReadiness.model_validate(dict(row)) for row in rows],
    )


async def list_campaign_readiness(campaign_id: str) -> ReadinessList:
    """All readiness rows for a campaign's accounts (bulk read for the board)."""
    return await asyncio.to_thread(_list_campaign_readiness, campaign_id)


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
