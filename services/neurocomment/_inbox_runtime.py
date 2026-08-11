"""Durable post dispatch and bounded recent-history recovery."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.config import settings
from core.db import (
    checkpoint_backfill,
    claim_pending_posts,
    complete_post,
    enqueue_post_bounded,
    next_pending_attempt_unix,
    prepare_backfill,
    release_post,
    requeue_processing_posts,
    return_claimed_posts,
)
from core.logging import log_event
from core.telegram_client import fetch_recent_posts
from schemas.neurocomment_pipeline import PipelineOutcome

if TYPE_CHECKING:
    from core.repositories.neurocomment._inbox import BackfillPlan
    from schemas.telegram_actions import NewPostEvent


async def on_post(event: NewPostEvent) -> None:
    """Persist before returning to Telethon, then fill free worker slots."""
    cutoff = int(datetime.now(UTC).timestamp() - settings.neurocomment.post_backfill_ttl_seconds)
    status = await enqueue_post_bounded(
        event,
        settings.neurocomment.post_inbox_max_pending,
        cutoff,
    )
    if status == "inserted":
        await _dispatch_pending()
    elif status == "full":
        await log_event(
            "WARNING",
            "neurocomment_inbox_queue_full",
            extra={"channel": event.channel, "post_id": event.post_id},
        )


async def start_inbox(*, recover_processing: bool = False) -> None:
    from services.neurocomment import _runtime  # noqa: PLC0415

    async with _runtime._inbox_dispatch_lock():  # noqa: SLF001
        _runtime._INBOX_GENERATION += 1  # noqa: SLF001
        _runtime._INBOX_ACCEPTING = True  # noqa: SLF001
        retry_task, _runtime._INBOX_RETRY_TASK = _runtime._INBOX_RETRY_TASK, None  # noqa: SLF001
    await _runtime._cancel_bounded(retry_task)  # noqa: SLF001
    if recover_processing:
        await requeue_processing_posts()
    await _dispatch_pending()


async def recover_inbox() -> None:
    """Return crash-orphaned rows to pending without resuming a paused runtime."""
    await requeue_processing_posts()


async def stop_inbox() -> None:
    from services.neurocomment import _runtime  # noqa: PLC0415

    # Close immediately, then commit the gate under the same lock as claim/publish.
    # A dispatcher already queued on the lock re-checks this value before claiming.
    _runtime._INBOX_ACCEPTING = False  # noqa: SLF001
    _runtime._INBOX_GENERATION += 1  # noqa: SLF001
    async with _runtime._inbox_dispatch_lock():  # noqa: SLF001
        _runtime._INBOX_ACCEPTING = False  # noqa: SLF001
        retry_task, _runtime._INBOX_RETRY_TASK = _runtime._INBOX_RETRY_TASK, None  # noqa: SLF001
    await _runtime._cancel_bounded(retry_task)  # noqa: SLF001
    await stop_backfill()


async def _dispatch_pending() -> None:
    from services.neurocomment import _runtime  # noqa: PLC0415

    async with _runtime._inbox_dispatch_lock():  # noqa: SLF001
        if not _runtime._INBOX_ACCEPTING:  # noqa: SLF001
            return
        generation = _runtime._INBOX_GENERATION  # noqa: SLF001
        capacity = settings.neurocomment.max_concurrent_post_tasks - len(_runtime._TASKS)  # noqa: SLF001
        if capacity <= 0:
            _drop_retry_timer_locked()
            return
        cutoff = int(
            datetime.now(UTC).timestamp() - settings.neurocomment.post_backfill_ttl_seconds
        )
        claimed = await claim_pending_posts(capacity, cutoff)
        if (  # Stop may close the gate while the SQLite claim runs in ``to_thread``.
            not _runtime._INBOX_ACCEPTING  # noqa: SLF001
            or generation != _runtime._INBOX_GENERATION  # noqa: SLF001
        ):
            await return_claimed_posts(claimed)
            return
        for event in claimed:
            task = asyncio.create_task(_run_one(event, _runtime._RUNTIME_GENERATION))  # noqa: SLF001
            _runtime._TASKS.add(task)  # noqa: SLF001
        if len(_runtime._TASKS) >= settings.neurocomment.max_concurrent_post_tasks:  # noqa: SLF001
            _drop_retry_timer_locked()
        else:
            await _arm_retry_timer_locked()


def _drop_retry_timer_locked() -> None:
    """Detach/cancel the owned deadline timer while the dispatch lock is held."""
    from services.neurocomment import _runtime  # noqa: PLC0415

    task, _runtime._INBOX_RETRY_TASK = _runtime._INBOX_RETRY_TASK, None  # noqa: SLF001
    if task is None or task is asyncio.current_task() or task.done():
        return
    task.cancel()
    _runtime._retain_until_done(task)  # noqa: SLF001


async def _sleep_until(deadline_unix: int) -> None:
    delay = max(0.0, deadline_unix - datetime.now(UTC).timestamp())
    await asyncio.sleep(delay)


async def _arm_retry_timer_locked() -> None:
    """Own one wake-up for the earliest durable retry deadline."""
    from services.neurocomment import _runtime  # noqa: PLC0415

    deadline = await next_pending_attempt_unix()
    _drop_retry_timer_locked()
    if deadline is None or not _runtime._INBOX_ACCEPTING:  # noqa: SLF001
        return
    generation = _runtime._INBOX_GENERATION  # noqa: SLF001

    async def _wake() -> None:
        await _sleep_until(deadline)
        if (  # The timer may finish after Stop/Start replaced its generation.
            not _runtime._INBOX_ACCEPTING  # noqa: SLF001
            or generation != _runtime._INBOX_GENERATION  # noqa: SLF001
        ):
            return
        await _dispatch_pending()

    task = asyncio.create_task(_wake())
    _runtime._INBOX_RETRY_TASK = task  # noqa: SLF001

    def _clear(completed: asyncio.Task[None]) -> None:
        if _runtime._INBOX_RETRY_TASK is completed:  # noqa: SLF001
            _runtime._INBOX_RETRY_TASK = None  # noqa: SLF001

    task.add_done_callback(_clear)


async def _retry(event: NewPostEvent) -> None:
    """Retry only proven pre-send failures with exponential bounded backoff."""
    nc = settings.neurocomment
    # Attempts were incremented by the claim. The repository owns the final cap; using
    # the configured maximum here gives an upper bound without another hot-path read.
    await release_post(
        event,
        nc.post_inbox_max_attempts,
        nc.post_inbox_retry_base_seconds,
        nc.post_inbox_retry_max_seconds,
    )


async def _run_one(event: NewPostEvent, generation: int) -> None:
    from services.neurocomment import _runtime  # noqa: PLC0415

    token = _runtime._WORKER_GENERATION.set(generation)  # noqa: SLF001
    try:
        if generation != _runtime._RUNTIME_GENERATION:  # noqa: SLF001
            await _retry(event)
            return
        outcome = await _runtime.handle_new_post(event)
        if outcome == PipelineOutcome.RETRYABLE:
            await _retry(event)
        else:
            await complete_post(
                event,
                outcome if isinstance(outcome, PipelineOutcome) else PipelineOutcome.TERMINAL,
            )
    except asyncio.CancelledError:
        await _retry(event)
        raise
    except Exception as exc:  # noqa: BLE001 - isolate one durable worker
        # An exception escaping the typed engine occurred outside a known dispatch
        # boundary. Retry is bounded; dispatch ambiguity is converted inside engine.
        await _retry(event)
        await log_event(
            "ERROR",
            "neurocomment_inbox_worker_failed",
            extra={
                "channel": event.channel,
                "post_id": event.post_id,
                "error_type": type(exc).__name__,
            },
        )
    finally:
        _runtime._WORKER_GENERATION.reset(token)  # noqa: SLF001
        task = asyncio.current_task()
        if task is not None:
            _runtime._TASKS.discard(task)  # noqa: SLF001
        if _runtime._INBOX_ACCEPTING:  # noqa: SLF001
            await _dispatch_pending()


async def prepare_backfill_plans(channels: list[str]) -> dict[str, BackfillPlan]:
    return await prepare_backfill(channels, settings.neurocomment.post_backfill_interval_seconds)


async def ensure_backfill(
    listener_account_id: str,
    channels: list[str],
    plans: dict[str, BackfillPlan] | None = None,
) -> None:
    """Replace any older recovery pass; the newest subscription owns recovery."""
    from services.neurocomment import _runtime  # noqa: PLC0415

    if not _runtime._RUNTIME_OWNER_INITIALIZED:  # noqa: SLF001 - direct seam compatibility
        _runtime._activate_runtime_owner(listener_account_id)  # noqa: SLF001
    _runtime._BACKFILL_GENERATION += 1  # noqa: SLF001
    generation = _runtime._BACKFILL_GENERATION  # noqa: SLF001
    owner_generation = _runtime._RUNTIME_GENERATION  # noqa: SLF001
    previous = _runtime._BACKFILL_TASK  # noqa: SLF001
    if previous is not None and not previous.done():
        previous.cancel()
        _runtime._retain_until_done(previous)  # noqa: SLF001
    if _runtime._BACKFILL_TIMER_TASK is not None:  # noqa: SLF001
        _runtime._BACKFILL_TIMER_TASK.cancel()  # noqa: SLF001
        _runtime._retain_until_done(_runtime._BACKFILL_TIMER_TASK)  # noqa: SLF001
        _runtime._BACKFILL_TIMER_TASK = None  # noqa: SLF001
    raw_plans = plans or await prepare_backfill_plans(channels)
    bounded = [channel for channel in channels if channel in raw_plans][
        : settings.neurocomment.post_backfill_max_channels
    ]
    if not bounded:
        _runtime._BACKFILL_TASK = None  # noqa: SLF001
        _schedule_periodic(listener_account_id, channels, generation, owner_generation)
        return
    _runtime._BACKFILL_TASK = asyncio.create_task(  # noqa: SLF001
        _backfill(
            listener_account_id,
            bounded,
            raw_plans,
            generation,
            owner_generation,
        ),
    )


async def _backfill(  # noqa: C901, PLR0912 - bounded page/TTL/ownership state machine
    listener_account_id: str,
    channels: list[str],
    plans: dict[str, BackfillPlan],
    generation: int,
    owner_generation: int,
) -> None:
    cutoff = int(datetime.now(UTC).timestamp() - settings.neurocomment.post_backfill_ttl_seconds)
    for index, channel in enumerate(channels):
        if not _backfill_is_current(
            listener_account_id,
            generation,
            owner_generation,
        ):
            return
        plan = plans[channel]
        floor = int(plan.floor_post_id)
        before = plan.before_post_id
        success = False
        try:
            for page in range(settings.neurocomment.post_backfill_max_pages_per_channel):
                if not _backfill_is_current(
                    listener_account_id,
                    generation,
                    owner_generation,
                ):
                    return
                kwargs: dict[str, int] = {
                    "limit": settings.neurocomment.post_backfill_limit_per_channel,
                }
                if before is not None:
                    kwargs["before_post_id"] = int(before)
                posts = await fetch_recent_posts(listener_account_id, channel, **kwargs)
                if not _backfill_is_current(
                    listener_account_id,
                    generation,
                    owner_generation,
                ):
                    return
                if not posts:
                    success = True
                    break
                for post in reversed(posts):
                    if not _backfill_is_current(
                        listener_account_id,
                        generation,
                        owner_generation,
                    ):
                        return
                    if post.post_id > floor and post.date_unix >= cutoff:
                        await on_post(post)
                oldest = min(posts, key=lambda post: post.post_id)
                reached_floor = any(post.post_id <= floor for post in posts)
                reached_ttl = any(not post.date_unix or post.date_unix < cutoff for post in posts)
                if (
                    reached_floor
                    or reached_ttl
                    or len(posts) < settings.neurocomment.post_backfill_limit_per_channel
                ):
                    success = True
                    break
                before = oldest.post_id
                if page + 1 == settings.neurocomment.post_backfill_max_pages_per_channel:
                    break
        except Exception as exc:  # noqa: BLE001 - isolate one inaccessible channel
            await log_event(
                "WARNING",
                "neurocomment_post_backfill_failed",
                account_id=listener_account_id,
                extra={"channel": channel, "error_type": type(exc).__name__},
            )
        if not _backfill_is_current(
            listener_account_id,
            generation,
            owner_generation,
        ):
            return
        await checkpoint_backfill(
            channel,
            before_post_id=None if success else before,
            success=success,
            retry_seconds=settings.neurocomment.post_backfill_retry_seconds,
        )
        await _dispatch_pending()
        if index + 1 < len(channels) and settings.neurocomment.post_backfill_channel_delay_seconds:
            await asyncio.sleep(settings.neurocomment.post_backfill_channel_delay_seconds)
    if _backfill_is_current(listener_account_id, generation, owner_generation):
        _schedule_periodic(listener_account_id, channels, generation, owner_generation)


def _backfill_is_current(
    listener_account_id: str,
    generation: int,
    owner_generation: int,
) -> bool:
    from services.neurocomment import _runtime  # noqa: PLC0415

    return generation == _runtime._BACKFILL_GENERATION and _runtime._runtime_owner_is_current(  # noqa: SLF001
        listener_account_id,
        owner_generation,
    )


def _schedule_periodic(
    listener_account_id: str,
    channels: list[str],
    generation: int,
    owner_generation: int,
) -> None:
    from services.neurocomment import _runtime  # noqa: PLC0415

    async def _later() -> None:
        await asyncio.sleep(settings.neurocomment.post_backfill_retry_seconds)
        if not _backfill_is_current(listener_account_id, generation, owner_generation):
            return
        await ensure_backfill(listener_account_id, channels)

    _runtime._BACKFILL_TIMER_TASK = asyncio.create_task(_later())  # noqa: SLF001


async def stop_backfill() -> None:
    from services.neurocomment import _runtime  # noqa: PLC0415

    _runtime._BACKFILL_GENERATION += 1  # noqa: SLF001
    timer, _runtime._BACKFILL_TIMER_TASK = _runtime._BACKFILL_TIMER_TASK, None  # noqa: SLF001
    task, _runtime._BACKFILL_TASK = _runtime._BACKFILL_TASK, None  # noqa: SLF001
    await _runtime._cancel_bounded(task, timer)  # noqa: SLF001
