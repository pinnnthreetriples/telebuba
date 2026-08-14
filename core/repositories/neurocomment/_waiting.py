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
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select, update

from core.config import settings
from core.db import _get_engine, _now_iso
from core.repositories.neurocomment._comments import (
    _claim_values,
    _insert_comment,
)
from core.repositories.neurocomment._tables import _neurocomment_comments, _neurocomment_inbox
from schemas.neurocomment import CommentRecord
from schemas.neurocomment_pipeline import InboxStage

if TYPE_CHECKING:
    from schemas.telegram_actions import NewPostEvent

ReplyStage = Literal["waiting", "pre_send", "dispatching", "dispatched"]


class WaitingReplyRecord(CommentRecord):
    """Internal durable queue row; reply metadata is not part of the public API model."""

    reply_deadline_at: str = Field(min_length=1)
    reply_attempts: int = Field(ge=0)


class WaitingReplyList(BaseModel):
    """Fleet-wide durable reply work list."""

    comments: list[WaitingReplyRecord] = Field(default_factory=list)


async def park_comment(
    channel: str,
    post_id: int,
    campaign_id: str,
    account_id: str,
    *,
    wait_minutes: int | None = None,
) -> bool:
    """Reserve ``(channel, post_id)`` as ``waiting`` instead of commenting now; ``True`` if won.

    Deliberately the SAME single conditional INSERT as :func:`claim_comment`, only the
    status differs, because that INSERT *is* the double-comment guard: a re-delivered
    listener update or a restart replaying the post loses the conflict and gets ``False``,
    whether the winner is sending now or parked. A second write path here would have to
    re-earn that guarantee, and any read-then-insert version would not have it at all.

    The deadline is frozen with the claim. A settings change affects later posts only;
    an already parked post keeps the exact wait promised when it was accepted.
    """
    return await asyncio.to_thread(
        _park_comment,
        channel,
        post_id,
        campaign_id,
        account_id,
        settings.neurocomment.reply_wait_minutes if wait_minutes is None else wait_minutes,
    )


def _park_comment(
    channel: str,
    post_id: int,
    campaign_id: str,
    account_id: str,
    wait_minutes: int,
) -> bool:
    values = _claim_values(channel, post_id, campaign_id, account_id, "waiting")
    deadline_at = (
        datetime.fromisoformat(str(values["created_at"])) + timedelta(minutes=wait_minutes)
    ).isoformat()
    values.update(
        reply_state="waiting",
        reply_stage="waiting",
        reply_outcome=None,
        reply_attempts=0,
        reply_deadline_at=deadline_at,
    )
    return _insert_comment(values)


def _list_waiting_comments() -> WaitingReplyList:
    statement = select(_neurocomment_comments).where(
        (_neurocomment_comments.c.status == "waiting")
        & (_neurocomment_comments.c.reply_state == "waiting"),
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return WaitingReplyList(
        comments=[WaitingReplyRecord.model_validate(dict(row)) for row in rows],
    )


async def list_waiting_comments() -> WaitingReplyList:
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
                & (_neurocomment_comments.c.status == "waiting")
                & (_neurocomment_comments.c.reply_state == "waiting"),
            )
            .values(
                status="claimed",
                reply_state="reply_processing",
                reply_stage="pre_send",
                reply_outcome=None,
                reply_attempts=_neurocomment_comments.c.reply_attempts + 1,
                updated_at=_now_iso(),
            ),
        )
    return result.rowcount > 0


async def promote_waiting_to_claimed(channel: str, post_id: int) -> bool:
    """Take a parked post out of the wait into an in-flight claim; ``True`` if it is ours.

    The other half of the guard :func:`park_comment` opens. Parking wins the post once, but
    the wait then outlives the process, so the row gets read again and again: two sweep
    ticks overlapping, or a tick racing the startup sweep, both see the same ``waiting`` row
    and without this only-one-wins UPDATE both would reply under one post. ``False`` means
    somebody else already took it — the caller must send nothing.

    Public ``status`` becomes ``claimed`` for quota/backward compatibility; durable
    ``reply_state`` becomes ``reply_processing`` at the proven pre-send stage. The
    ``updated_at`` bump starts crash recovery from the attempt, never the original wait.
    """
    return await asyncio.to_thread(_promote_waiting_to_claimed, channel, post_id)


def _mark_reply_stage(channel: str, post_id: int, stage: ReplyStage) -> bool:
    if stage not in ("dispatching", "dispatched"):
        return False
    prior = "pre_send" if stage == "dispatching" else "dispatching"
    with _get_engine().begin() as connection:
        result = connection.execute(
            update(_neurocomment_comments)
            .where(
                (_neurocomment_comments.c.channel == channel)
                & (_neurocomment_comments.c.post_id == post_id)
                & (_neurocomment_comments.c.status == "claimed")
                & (_neurocomment_comments.c.reply_state == "reply_processing")
                & (_neurocomment_comments.c.reply_stage == prior)
            )
            .values(reply_stage=stage, updated_at=_now_iso()),
        )
    return result.rowcount > 0


async def mark_reply_stage(channel: str, post_id: int, stage: ReplyStage) -> bool:
    """Advance a reply's durable send boundary; terminal/foreign rows never move."""
    return await asyncio.to_thread(_mark_reply_stage, channel, post_id, stage)


def _set_comment_dispatch_stage(event: NewPostEvent, stage: ReplyStage) -> bool:
    """Atomically advance inbox and optional durable-reply dispatch ownership."""
    if stage not in ("dispatching", "dispatched"):
        return False
    prior = "pre_send" if stage == "dispatching" else "dispatching"
    with _get_engine().begin() as connection:
        row = (
            connection.execute(
                select(
                    _neurocomment_comments.c.status,
                    _neurocomment_comments.c.reply_state,
                    _neurocomment_comments.c.reply_stage,
                ).where(
                    (_neurocomment_comments.c.channel == event.channel)
                    & (_neurocomment_comments.c.post_id == event.post_id),
                ),
            )
            .mappings()
            .first()
        )
        if row is None or row["status"] != "claimed":
            return False
        if row["reply_state"] is not None:
            reply = connection.execute(
                update(_neurocomment_comments)
                .where(
                    (_neurocomment_comments.c.channel == event.channel)
                    & (_neurocomment_comments.c.post_id == event.post_id)
                    & (_neurocomment_comments.c.status == "claimed")
                    & (_neurocomment_comments.c.reply_state == "reply_processing")
                    & (_neurocomment_comments.c.reply_stage == prior)
                )
                .values(reply_stage=stage, updated_at=_now_iso()),
            )
            if reply.rowcount == 0:
                return False
        connection.execute(
            update(_neurocomment_inbox)
            .where(
                (_neurocomment_inbox.c.channel == event.channel)
                & (_neurocomment_inbox.c.post_id == event.post_id)
                & (_neurocomment_inbox.c.state == "processing")
            )
            .values(stage=InboxStage(stage), updated_at=_now_iso()),
        )
    return True


async def set_comment_dispatch_stage(event: NewPostEvent, stage: ReplyStage) -> bool:
    """Commit a send boundary only while the comment claim is still owned."""
    return await asyncio.to_thread(_set_comment_dispatch_stage, event, stage)
