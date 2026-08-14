"""Promote and dispatch a parked Neurocomment reply.

The waiting pass decides *whether* a post is due and *which* reader to answer.
This module owns the state transition that grants one sweep tick the send, rebuilds
the listener event, and hands that owned attempt to the shared generation pipeline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from core.db import mark_comment_failed, release_claim
from core.logging import log_event
from schemas.neurocomment_pipeline import PipelineOutcome
from schemas.telegram_actions import NewPostEvent

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from schemas.neurocomment import CommentRecord, NeurocommentCampaign, NeurocommentSettings
    from schemas.telegram_actions_comments import PostCommentRecord, ReadPostCommentsResult


class Promoted(NamedTuple):
    """Proof that a parked row won the ``waiting -> claimed`` transition."""

    row: CommentRecord


async def promote(
    row: CommentRecord,
    promote_waiting: Callable[[str, int], Awaitable[bool]],
) -> Promoted | None:
    """Take a parked row for this tick; return ``None`` when another tick won."""
    if await promote_waiting(row.channel, row.post_id):
        return Promoted(row)
    return None


class Choice(NamedTuple):
    """The selected reader and their position among eligible strangers."""

    target: PostCommentRecord | None
    index: int
    total: int
    unread: bool = False


def rebuild_event(row: CommentRecord, read: ReadPostCommentsResult | None) -> NewPostEvent:
    """Rebuild the listener event from the durable row and its thread read."""
    return NewPostEvent(
        channel=row.channel,
        post_id=row.post_id,
        text=read.post_text if read is not None else "",
        media_kind=read.post_media_kind if read is not None and read.post_media_kind else "none",
    )


async def reply_and_post(
    promoted: Promoted,
    campaign: NeurocommentCampaign,
    event: NewPostEvent,
    choice: Choice,
    limits: NeurocommentSettings,
) -> None:
    """Log the decision and hand an owned reply to the shared pipeline back half."""
    row = promoted.row
    if choice.target is None:
        await log_event(
            "INFO",
            "neurocomment_reply_wait_expired",
            account_id=row.account_id,
            extra={
                "channel": row.channel,
                "post_id": row.post_id,
                "waited_minutes": limits.reply_wait_minutes,
                "reason": "thread_unread" if choice.unread else "no_readers",
            },
        )
    else:
        await log_event(
            "INFO",
            "neurocomment_reply_to_human",
            account_id=row.account_id,
            extra={
                "channel": row.channel,
                "post_id": row.post_id,
                "stranger_index": choice.index,
                "stranger_count": choice.total,
            },
        )

    # Late import: engine imports the waiting entry point for the park branch.
    from services.neurocomment import engine  # noqa: PLC0415

    try:
        outcome = await engine._generate_and_post(  # noqa: SLF001 - same domain back half.
            event,
            campaign,
            row.account_id,
            limits,
            target=choice.target,
        )
        if outcome == PipelineOutcome.RETRYABLE:
            await release_claim(row.channel, row.post_id)
    except BaseException:
        # An escaping error is proven pre-send unless the guarded release says the
        # durable dispatch boundary was already crossed.
        if not await release_claim(row.channel, row.post_id):
            await mark_comment_failed(row.channel, row.post_id)
        raise
