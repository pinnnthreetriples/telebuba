"""Durable new-post inbox and high-water cursors."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import NamedTuple, cast

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core.db import _get_engine, _now_iso
from core.repositories.neurocomment._tables import (
    _neurocomment_comments,
    _neurocomment_cursors,
    _neurocomment_inbox,
)
from schemas.neurocomment_pipeline import InboxStage, PipelineOutcome
from schemas.telegram_actions import NewPostEvent


def _event_values(event: NewPostEvent, now: str) -> dict[str, object]:
    date_unix = event.date_unix or int(datetime.now(UTC).timestamp())
    return {
        "channel": event.channel,
        "post_id": event.post_id,
        "date_unix": date_unix,
        "text": event.text,
        "media_kind": event.media_kind,
        "is_forward": event.is_forward,
        "state": "pending",
        "stage": InboxStage.RECEIVED,
        "outcome": None,
        "attempts": 0,
        "next_attempt_unix": 0,
        "received_at": now,
        "updated_at": now,
    }


def _enqueue_post(event: NewPostEvent) -> bool:
    """Insert once and advance the channel cursor in the same transaction."""
    now = _now_iso()
    inbox = sqlite_insert(_neurocomment_inbox).values(**_event_values(event, now))
    cursor = sqlite_insert(_neurocomment_cursors).values(
        channel=event.channel,
        last_post_id=event.post_id,
        updated_at=now,
    )
    with _get_engine().begin() as connection:
        result = connection.execute(inbox.on_conflict_do_nothing())
        connection.execute(
            cursor.on_conflict_do_update(
                index_elements=[_neurocomment_cursors.c.channel],
                set_={
                    "last_post_id": cursor.excluded.last_post_id,
                    "updated_at": now,
                },
                where=cursor.excluded.last_post_id > _neurocomment_cursors.c.last_post_id,
            ),
        )
    return result.rowcount == 1


async def enqueue_post(event: NewPostEvent) -> bool:
    """Persist one live/backfilled post. False means it was already known."""
    return await asyncio.to_thread(_enqueue_post, event)


def _enqueue_post_bounded(event: NewPostEvent, max_pending: int, cutoff_unix: int) -> str:
    """Insert, dedupe, or reject at the durable queue cap in one transaction."""
    now = _now_iso()
    inbox = sqlite_insert(_neurocomment_inbox).values(**_event_values(event, now))
    cursor = sqlite_insert(_neurocomment_cursors).values(
        channel=event.channel,
        last_post_id=event.post_id,
        updated_at=now,
    )
    with _get_engine().begin() as connection:
        connection.execute(
            update(_neurocomment_inbox)
            .where(
                (_neurocomment_inbox.c.state == "pending")
                & (_neurocomment_inbox.c.date_unix < cutoff_unix),
            )
            .values(state="expired", outcome="expired", updated_at=now),
        )
        known = connection.execute(
            select(_neurocomment_inbox.c.post_id).where(
                (_neurocomment_inbox.c.channel == event.channel)
                & (_neurocomment_inbox.c.post_id == event.post_id),
            ),
        ).first()
        if known is not None:
            return "duplicate"
        active = connection.execute(
            select(func.count()).where(
                _neurocomment_inbox.c.state.in_(("pending", "processing")),
            ),
        ).scalar_one()
        if active >= max_pending:
            return "full"
        connection.execute(inbox)
        connection.execute(
            cursor.on_conflict_do_update(
                index_elements=[_neurocomment_cursors.c.channel],
                set_={"last_post_id": cursor.excluded.last_post_id, "updated_at": now},
                where=cursor.excluded.last_post_id > _neurocomment_cursors.c.last_post_id,
            ),
        )
    return "inserted"


async def enqueue_post_bounded(event: NewPostEvent, max_pending: int, cutoff_unix: int) -> str:
    return await asyncio.to_thread(_enqueue_post_bounded, event, max_pending, cutoff_unix)


def _claim_pending_posts(limit: int, cutoff_unix: int) -> list[NewPostEvent]:
    if limit <= 0:
        return []
    now = _now_iso()
    now_unix = int(datetime.now(UTC).timestamp())
    with _get_engine().begin() as connection:
        connection.execute(
            update(_neurocomment_inbox)
            .where(
                (_neurocomment_inbox.c.state == "pending")
                & (_neurocomment_inbox.c.date_unix < cutoff_unix),
            )
            .values(state="expired", updated_at=now),
        )
        rows = (
            connection.execute(
                select(_neurocomment_inbox)
                .where(
                    (_neurocomment_inbox.c.state == "pending")
                    & (_neurocomment_inbox.c.date_unix >= cutoff_unix)
                    & (_neurocomment_inbox.c.next_attempt_unix <= now_unix),
                )
                .order_by(_neurocomment_inbox.c.date_unix, _neurocomment_inbox.c.post_id)
                .limit(limit),
            )
            .mappings()
            .all()
        )
        claimed: list[dict[str, object]] = []
        for row in rows:
            result = connection.execute(
                update(_neurocomment_inbox)
                .where(
                    (_neurocomment_inbox.c.channel == row["channel"])
                    & (_neurocomment_inbox.c.post_id == row["post_id"])
                    & (_neurocomment_inbox.c.state == "pending"),
                )
                .values(
                    state="processing",
                    stage=InboxStage.PRE_SEND,
                    outcome=None,
                    attempts=_neurocomment_inbox.c.attempts + 1,
                    updated_at=now,
                ),
            )
            if result.rowcount == 1:
                claimed.append(dict(row))
    return [
        NewPostEvent(
            channel=str(row["channel"]),
            post_id=int(cast("int | str", row["post_id"])),
            date_unix=int(cast("int | str", row["date_unix"])),
            text=str(row["text"]),
            media_kind=str(row["media_kind"]),  # ty: ignore[invalid-argument-type]
            is_forward=bool(row["is_forward"]),
        )
        for row in claimed
    ]


async def claim_pending_posts(limit: int, cutoff_unix: int) -> list[NewPostEvent]:
    return await asyncio.to_thread(_claim_pending_posts, limit, cutoff_unix)


def _return_claimed_posts(events: list[NewPostEvent]) -> None:
    """Undo inbox-only claims when Stop wins before workers are published."""
    if not events:
        return
    now = _now_iso()
    with _get_engine().begin() as connection:
        for event in events:
            connection.execute(
                update(_neurocomment_inbox)
                .where(
                    (_neurocomment_inbox.c.channel == event.channel)
                    & (_neurocomment_inbox.c.post_id == event.post_id)
                    & (_neurocomment_inbox.c.state == "processing")
                    & (_neurocomment_inbox.c.stage == InboxStage.PRE_SEND),
                )
                .values(
                    state="pending",
                    stage=InboxStage.RECEIVED,
                    attempts=func.max(_neurocomment_inbox.c.attempts - 1, 0),
                    next_attempt_unix=0,
                    updated_at=now,
                ),
            )


async def return_claimed_posts(events: list[NewPostEvent]) -> None:
    await asyncio.to_thread(_return_claimed_posts, events)


def _next_pending_attempt_unix() -> int | None:
    """Return the earliest durable retry deadline, or ``None`` when none are pending."""
    with _get_engine().connect() as connection:
        value = connection.execute(
            select(func.min(_neurocomment_inbox.c.next_attempt_unix)).where(
                _neurocomment_inbox.c.state == "pending",
            ),
        ).scalar_one()
    return None if value is None else int(value)


async def next_pending_attempt_unix() -> int | None:
    return await asyncio.to_thread(_next_pending_attempt_unix)


def _settle_post(event: NewPostEvent, state: str, outcome: str | None = None) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            update(_neurocomment_inbox)
            .where(
                (_neurocomment_inbox.c.channel == event.channel)
                & (_neurocomment_inbox.c.post_id == event.post_id)
                & (_neurocomment_inbox.c.state == "processing"),
            )
            .values(state=state, outcome=outcome, updated_at=_now_iso()),
        )


async def complete_post(
    event: NewPostEvent,
    outcome: PipelineOutcome = PipelineOutcome.TERMINAL,
) -> None:
    await asyncio.to_thread(_settle_post, event, "done", outcome)


def _release_post(
    event: NewPostEvent,
    max_attempts: int,
    retry_base_seconds: float,
    retry_max_seconds: float,
) -> bool:
    """Requeue a proven pre-send failure, or exhaust its bounded retry budget."""
    with _get_engine().begin() as connection:
        row = connection.execute(
            select(_neurocomment_inbox.c.attempts, _neurocomment_inbox.c.stage).where(
                (_neurocomment_inbox.c.channel == event.channel)
                & (_neurocomment_inbox.c.post_id == event.post_id)
                & (_neurocomment_inbox.c.state == "processing"),
            ),
        ).one_or_none()
        if row is None:
            return False
        attempts, stage = int(row.attempts), str(row.stage)
        if stage not in (InboxStage.RECEIVED, InboxStage.PRE_SEND):
            connection.execute(
                update(_neurocomment_inbox)
                .where(
                    (_neurocomment_inbox.c.channel == event.channel)
                    & (_neurocomment_inbox.c.post_id == event.post_id)
                    & (_neurocomment_inbox.c.state == "processing"),
                )
                .values(
                    state="done",
                    outcome=PipelineOutcome.AMBIGUOUS,
                    updated_at=_now_iso(),
                ),
            )
            return False
        # PRE_SEND is durable proof that Telegram dispatch never began. Clear a claim in
        # this SAME transaction as the inbox transition: if engine's own release failed,
        # the next retry must still be able to win instead of terminally losing the post.
        connection.execute(
            delete(_neurocomment_comments).where(
                (_neurocomment_comments.c.channel == event.channel)
                & (_neurocomment_comments.c.post_id == event.post_id)
                & (_neurocomment_comments.c.status == "claimed"),
            ),
        )
        exhausted = attempts >= max_attempts
        delay = min(retry_max_seconds, retry_base_seconds * (2 ** max(0, attempts - 1)))
        retry_at_unix = int(datetime.now(UTC).timestamp() + delay)
        connection.execute(
            update(_neurocomment_inbox)
            .where(
                (_neurocomment_inbox.c.channel == event.channel)
                & (_neurocomment_inbox.c.post_id == event.post_id)
                & (_neurocomment_inbox.c.state == "processing"),
            )
            .values(
                state="done" if exhausted else "pending",
                stage=InboxStage.RECEIVED,
                outcome="retry_exhausted" if exhausted else PipelineOutcome.RETRYABLE,
                next_attempt_unix=retry_at_unix,
                updated_at=_now_iso(),
            ),
        )
    return not exhausted


async def release_post(
    event: NewPostEvent,
    max_attempts: int,
    retry_base_seconds: float,
    retry_max_seconds: float,
) -> bool:
    return await asyncio.to_thread(
        _release_post,
        event,
        max_attempts,
        retry_base_seconds,
        retry_max_seconds,
    )


def _mark_inbox_stage(event: NewPostEvent, stage: InboxStage) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            update(_neurocomment_inbox)
            .where(
                (_neurocomment_inbox.c.channel == event.channel)
                & (_neurocomment_inbox.c.post_id == event.post_id)
                & (_neurocomment_inbox.c.state == "processing"),
            )
            .values(stage=stage, updated_at=_now_iso()),
        )


async def mark_inbox_stage(event: NewPostEvent, stage: InboxStage) -> None:
    await asyncio.to_thread(_mark_inbox_stage, event, stage)


def _requeue_processing_posts() -> int:
    with _get_engine().begin() as connection:
        safe_rows = connection.execute(
            select(_neurocomment_inbox.c.channel, _neurocomment_inbox.c.post_id).where(
                (_neurocomment_inbox.c.state == "processing")
                & (_neurocomment_inbox.c.stage.in_((InboxStage.RECEIVED, InboxStage.PRE_SEND))),
            ),
        ).all()
        for channel, post_id in safe_rows:
            # The durable stage proves no Telegram dispatch began, so a matching claim is
            # safe to release. This is what makes a crash just after claim resumable.
            connection.execute(
                delete(_neurocomment_comments).where(
                    (_neurocomment_comments.c.channel == channel)
                    & (_neurocomment_comments.c.post_id == post_id)
                    & (_neurocomment_comments.c.status == "claimed"),
                ),
            )
        safe = connection.execute(
            update(_neurocomment_inbox)
            .where(
                (_neurocomment_inbox.c.state == "processing")
                & (_neurocomment_inbox.c.stage.in_((InboxStage.RECEIVED, InboxStage.PRE_SEND))),
            )
            .values(
                state="pending",
                stage=InboxStage.RECEIVED,
                next_attempt_unix=0,
                updated_at=_now_iso(),
            ),
        )
        ambiguous = connection.execute(
            update(_neurocomment_inbox)
            .where(
                (_neurocomment_inbox.c.state == "processing")
                & (
                    _neurocomment_inbox.c.stage.in_(
                        (InboxStage.DISPATCHING, InboxStage.DISPATCHED),
                    )
                ),
            )
            .values(state="done", outcome=PipelineOutcome.AMBIGUOUS, updated_at=_now_iso()),
        )
    return safe.rowcount + ambiguous.rowcount


async def requeue_processing_posts() -> int:
    """Recover only process-orphaned work; comment claims remain the send fence."""
    return await asyncio.to_thread(_requeue_processing_posts)


class BackfillPlan(NamedTuple):
    floor_post_id: int
    before_post_id: int | None


def _prepare_backfill(channels: list[str], interval_seconds: float) -> dict[str, BackfillPlan]:
    now = datetime.now(UTC)
    now_iso = now.isoformat()
    plans: dict[str, BackfillPlan] = {}
    with _get_engine().begin() as connection:
        for channel in channels:
            connection.execute(
                sqlite_insert(_neurocomment_cursors)
                .values(channel=channel, last_post_id=0, updated_at=now_iso)
                .on_conflict_do_nothing(),
            )
            row = (
                connection.execute(
                    select(_neurocomment_cursors).where(_neurocomment_cursors.c.channel == channel),
                )
                .mappings()
                .one()
            )
            retry_at = row["backfill_retry_at"]
            if retry_at and datetime.fromisoformat(str(retry_at)) > now:
                continue
            floor = row["backfill_floor_post_id"]
            if floor is None:
                success_at = row["backfill_success_at"]
                if success_at and now - datetime.fromisoformat(str(success_at)) < timedelta(
                    seconds=interval_seconds,
                ):
                    continue
                floor = int(row["last_post_id"])
                connection.execute(
                    update(_neurocomment_cursors)
                    .where(_neurocomment_cursors.c.channel == channel)
                    .values(
                        backfill_floor_post_id=floor,
                        backfill_before_post_id=None,
                        updated_at=now_iso,
                    ),
                )
            before = row["backfill_before_post_id"]
            plans[channel] = BackfillPlan(int(floor), None if before is None else int(before))
    return plans


async def prepare_backfill(channels: list[str], interval_seconds: float) -> dict[str, BackfillPlan]:
    return await asyncio.to_thread(_prepare_backfill, channels, interval_seconds)


def _checkpoint_backfill(
    channel: str,
    *,
    before_post_id: int | None,
    success: bool,
    retry_seconds: float,
) -> None:
    now = datetime.now(UTC)
    values: dict[str, object] = {"updated_at": now.isoformat()}
    if success:
        values.update(
            backfill_floor_post_id=None,
            backfill_before_post_id=None,
            backfill_success_at=now.isoformat(),
            backfill_retry_at=None,
        )
    else:
        values.update(
            backfill_before_post_id=before_post_id,
            backfill_retry_at=(now + timedelta(seconds=retry_seconds)).isoformat(),
        )
    with _get_engine().begin() as connection:
        connection.execute(
            update(_neurocomment_cursors)
            .where(_neurocomment_cursors.c.channel == channel)
            .values(**values),
        )


async def checkpoint_backfill(
    channel: str,
    *,
    before_post_id: int | None,
    success: bool,
    retry_seconds: float,
) -> None:
    await asyncio.to_thread(
        _checkpoint_backfill,
        channel,
        before_post_id=before_post_id,
        success=success,
        retry_seconds=retry_seconds,
    )
