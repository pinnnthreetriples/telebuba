"""Claim heartbeat, release, and crash recovery for neurocomment attempts."""

from __future__ import annotations

import asyncio

from sqlalchemy import delete, update

from core.db import _get_engine, _now_iso
from core.repositories.neurocomment._tables import _neurocomment_comments


def _release_claim(channel: str, post_id: int) -> bool:
    with _get_engine().begin() as connection:
        reply = connection.execute(
            update(_neurocomment_comments)
            .where(
                (_neurocomment_comments.c.channel == channel)
                & (_neurocomment_comments.c.post_id == post_id)
                & (_neurocomment_comments.c.status == "claimed")
                & (_neurocomment_comments.c.reply_state == "reply_processing")
                & (_neurocomment_comments.c.reply_stage == "pre_send")
            )
            .values(
                status="waiting",
                reply_state="waiting",
                reply_stage="waiting",
                reply_outcome="retryable",
                updated_at=_now_iso(),
            ),
        )
        ordinary = connection.execute(
            delete(_neurocomment_comments).where(
                (_neurocomment_comments.c.channel == channel)
                & (_neurocomment_comments.c.post_id == post_id)
                & (_neurocomment_comments.c.status == "claimed")
                & (_neurocomment_comments.c.reply_state.is_(None)),
            ),
        )
    return reply.rowcount > 0 or ordinary.rowcount > 0


async def release_claim(channel: str, post_id: int) -> bool:
    """Release proven pre-send work without reopening a possibly delivered attempt.

    Ordinary claims are deleted, freeing quota immediately. A durable reply at its
    ``pre_send`` stage returns to ``waiting`` with the original creation time and deadline.
    Once dispatch began, this operation is deliberately a no-op.
    """
    return await asyncio.to_thread(_release_claim, channel, post_id)


def _touch_comment_claim(channel: str, post_id: int) -> bool:
    with _get_engine().begin() as connection:
        result = connection.execute(
            update(_neurocomment_comments)
            .where(
                (_neurocomment_comments.c.channel == channel)
                & (_neurocomment_comments.c.post_id == post_id)
                & (_neurocomment_comments.c.status == "claimed")
            )
            .values(updated_at=_now_iso()),
        )
    return result.rowcount > 0


async def touch_comment_claim(channel: str, post_id: int) -> bool:
    """Heartbeat a still-owned claim; false means the caller must not send."""
    return await asyncio.to_thread(_touch_comment_claim, channel, post_id)


def _reclaim_stale_claims(cutoff_iso: str) -> int:
    with _get_engine().begin() as connection:
        now = _now_iso()
        retryable = connection.execute(
            update(_neurocomment_comments)
            .where(
                (_neurocomment_comments.c.status == "claimed")
                & (_neurocomment_comments.c.reply_state == "reply_processing")
                & (_neurocomment_comments.c.reply_stage == "pre_send")
                & (_neurocomment_comments.c.updated_at < cutoff_iso)
            )
            .values(
                status="waiting",
                reply_state="waiting",
                reply_stage="waiting",
                reply_outcome="retryable",
                updated_at=now,
            ),
        )
        ambiguous = connection.execute(
            update(_neurocomment_comments)
            .where(
                (_neurocomment_comments.c.status == "claimed")
                & (_neurocomment_comments.c.reply_state == "reply_processing")
                & (_neurocomment_comments.c.reply_stage.in_(("dispatching", "dispatched")))
                & (_neurocomment_comments.c.updated_at < cutoff_iso)
            )
            .values(
                status="failed",
                reply_state="terminal",
                reply_outcome="ambiguous",
                updated_at=now,
            ),
        )
        ordinary = connection.execute(
            update(_neurocomment_comments)
            .where(
                (_neurocomment_comments.c.status == "claimed")
                & (_neurocomment_comments.c.reply_state.is_(None))
                & (_neurocomment_comments.c.updated_at < cutoff_iso)
            )
            .values(status="failed", updated_at=now),
        )
    return retryable.rowcount + ambiguous.rowcount + ordinary.rowcount


async def reclaim_stale_claims(cutoff_iso: str) -> int:
    """Recover pre-send replies and fail closed after a possible dispatch."""
    return await asyncio.to_thread(_reclaim_stale_claims, cutoff_iso)
