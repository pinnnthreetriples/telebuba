"""The scheduled-post publisher: one in-process worker, one post at a time.

The worker sleeps until the earliest due post, but never longer than
``max_sleep_seconds``: a create / cancel / reschedule wakes it at once through
an ``asyncio.Event``, and the cap covers everything that cannot signal (a
Windows sleep, a clock jump, a row removed with its account).

Strictly one post at a time, fleet-wide: a backlog after a restart leaves as a
paced trickle rather than a coordinated burst. The price is lateness of a few
minutes when many posts share one moment, which is why the UI says "not before".
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress

from core import scheduled_media
from core.config import settings
from core.logging import log_event
from core.repositories import scheduled_posts as repo
from services.scheduled_posts import _dispatch

logger = logging.getLogger(__name__)


class _State:
    task: asyncio.Task[None] | None = None
    wake: asyncio.Event | None = None
    accepting: bool = False
    last_sweep: float = 0.0


_state = _State()


def wake() -> None:
    """Re-plan now: a post was created, moved or cancelled."""
    if _state.wake is not None:
        _state.wake.set()


def _accepting() -> bool:
    return _state.accepting


async def _sweep() -> None:
    """Missed posts, row retention and the media store, at most once per grace."""
    from services.scheduled_posts._policy import collect_media_garbage  # noqa: PLC0415 - cycle

    config = settings.scheduled_posts
    await _mark_missed()
    await repo.purge_settled_before(int(time.time()) - config.retention_days * 86_400)
    await collect_media_garbage()
    _state.last_sweep = time.monotonic()


async def _recover() -> None:
    """Settle orphaned claims: queued ones go back, dispatching ones turn ambiguous.

    The worker is the only thing that claims, and it claims one post at a time,
    so between two posts any ``processing`` row is an orphan — left by a restart,
    or by a settle write that failed. Left alone it would block its account.
    """
    requeued, ambiguous = await repo.requeue_interrupted()
    for post in ambiguous:
        await log_event(
            "WARNING",
            "account_scheduled_post_ambiguous",
            account_id=post.account_id,
            extra={"kind": post.kind, "error_type": "unconfirmed"},
        )
    if requeued:
        logger.info("scheduled posts: %d interrupted claims requeued", requeued)


async def _mark_missed() -> None:
    cutoff = int(time.time()) - settings.scheduled_posts.missed_grace_seconds
    for post in await repo.mark_missed(cutoff):
        await log_event(
            "WARNING",
            "account_scheduled_post_missed",
            account_id=post.account_id,
            extra={"kind": post.kind},
        )


async def _step() -> float:
    """Publish the next due post, or answer how long to sleep before looking again."""
    config = settings.scheduled_posts
    await _recover()
    if time.monotonic() - _state.last_sweep >= config.media_gc_grace_seconds:
        await _sweep()
    else:
        await _mark_missed()
    post = await repo.claim_one(int(time.time()))
    if post is not None:
        await _dispatch.publish(post, accepting=_accepting)
        return 0.0
    due = await repo.next_due_unix()
    if due is None:
        return config.max_sleep_seconds
    return min(config.max_sleep_seconds, max(0.0, due - time.time()))


async def _worker() -> None:
    wake_event = _state.wake
    if wake_event is None:  # pragma: no cover - start() sets it before spawning
        return
    while _state.accepting:
        # Cleared BEFORE planning, so a wake that lands during the queries is kept.
        wake_event.clear()
        try:
            delay = await _step()
        except Exception as exc:  # the worker must outlive one bad pass
            logger.exception("scheduled post worker pass failed")
            await log_event(
                "ERROR",
                "account_scheduled_post_worker_failed",
                extra={"error_type": type(exc).__name__},
            )
            delay = settings.scheduled_posts.max_sleep_seconds
        if delay <= 0 or not _state.accepting:
            continue
        with suppress(TimeoutError):
            await asyncio.wait_for(wake_event.wait(), timeout=delay)


async def start_scheduled_posts() -> None:
    """Recover interrupted posts, sweep, and start the worker (lifespan startup)."""
    await _recover()
    await _sweep()
    _state.wake = asyncio.Event()
    _state.accepting = True
    _state.task = asyncio.create_task(_worker(), name="scheduled-posts")


async def shutdown_scheduled_posts() -> None:
    """Stop taking posts, let the one in flight finish, then cancel what remains."""
    _state.accepting = False
    wake()
    task, _state.task = _state.task, None
    if task is None:
        return
    done, _ = await asyncio.wait({task}, timeout=settings.scheduled_posts.shutdown_drain_seconds)
    if not done:
        task.cancel()
        await asyncio.wait({task}, timeout=settings.scheduled_posts.shutdown_drain_seconds)


def reset_for_tests() -> None:
    """Forget the worker, its event and the store lock: each binds to one loop."""
    global _state  # noqa: PLW0603 - one module-level runtime, reset between tests
    _state = _State()
    scheduled_media.reset_for_tests()
