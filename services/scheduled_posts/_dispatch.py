"""Publish ONE claimed scheduled post and settle its row.

The dispatch boundary is ``mark_dispatching``, written before ``execute``:
- before it nothing has left the process, so any interruption hands the post
  back to the queue;
- after it Telegram may have applied the post, so an interruption, or an
  ``unavailable`` answer the gateway reports as ``UNCONFIRMED_ERROR_TYPE``,
  settles as ``ambiguous`` and is never retried by itself.

``execute``, ``fetch_account`` and ``refresh_account_avatar`` are imported at
module scope so tests monkeypatch them here, on their owning module.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from contextlib import suppress
from typing import TYPE_CHECKING

from core import scheduled_media
from core.config import settings
from core.db import fetch_account
from core.logging import log_event
from core.repositories import scheduled_posts as repo
from core.telegram_client import UNCONFIRMED_ERROR_TYPE, execute, refresh_account_avatar
from schemas.telegram_actions import PostStory, SetProfilePhoto
from services.accounts._result import result_code
from services.accounts.profile_read import invalidate_account_profile_cache
from services.pacing import await_send_slot

if TYPE_CHECKING:
    from collections.abc import Callable

    from schemas.logs import LogLevel
    from schemas.scheduled_posts import ScheduledPost, ScheduledPostState
    from schemas.telegram_actions import ActionResult

logger = logging.getLogger(__name__)

_DOMAIN = "scheduled"
_GLOBAL_PACING_KEY = "scheduled:global"
# Waits Telegram answers with a duration: the post was refused, not applied.
_TIMED_WAITS = frozenset({"flood_wait", "slow_mode_wait", "premium_wait"})
# Spread retries of posts that hit the same wait, so they do not return as a burst.
_RETRY_JITTER_SECONDS = 60
_rng = random.SystemRandom()


def _now() -> int:
    return int(time.time())


def _deadline(post: ScheduledPost) -> int:
    return post.scheduled_for_unix + settings.scheduled_posts.missed_grace_seconds


async def _log(level: LogLevel, event: str, post: ScheduledPost, code: str | None = None) -> None:
    extra: dict[str, object] = {"kind": post.kind}
    if code is not None:
        extra["error_type"] = code
    await log_event(level, event, account_id=post.account_id, extra=extra)


async def _build_action(post: ScheduledPost) -> SetProfilePhoto | PostStory:
    contents = [await scheduled_media.read_media(name) for name in post.media_names]
    suffix = "." + post.media_names[0].rsplit(".", 1)[1]
    if post.story is None:
        return SetProfilePhoto(filename=f"photo{suffix}", content=contents[0])
    return PostStory(
        filename=f"story{suffix}",
        content=contents[0],
        media_kind=post.story.media_kind,
        caption=post.story.caption,
        privacy_preset=post.story.privacy_preset,
        period_seconds=post.story.period_seconds,
        protect_content=post.story.protect_content,
        extra_images=contents[1:],
        collage_layout=post.story.collage_layout,
    )


async def _precheck(post: ScheduledPost) -> tuple[ScheduledPostState, str] | None:
    """``(state, code)`` when the post must end without a Telegram call."""
    account = await fetch_account(post.account_id)
    if account is None:
        return "cancelled", "account_not_found"
    if account.status == "frozen":
        return "failed", "account_frozen"
    return None


async def _refused_early(post: ScheduledPost) -> bool:
    """Settle a post that cannot go out, BEFORE it takes a fleet-wide pacing slot.

    Fifty due posts of a frozen account would otherwise hold the whole queue for
    fifty gaps without a single Telegram call. Re-checked under the lock later.
    """
    refusal = await _precheck(post)
    if refusal is None:
        for name in post.media_names:
            if not await scheduled_media.touch_existing(name):
                refusal = ("failed", "scheduled_media_missing")
                break
    if refusal is None:
        return False
    state, code = refusal
    if await repo.settle_queued(post.post_id, state, code):
        await _log("WARNING", "account_scheduled_post_failed", post, code)
    return True


async def _retry_or_fail(post: ScheduledPost, next_at: int, code: str) -> None:
    """Try again at ``next_at`` unless that lands past the post's grace window."""
    if next_at > _deadline(post):
        await repo.finish_dispatched(post.post_id, "failed", error_code=code)
        await _log("WARNING", "account_scheduled_post_failed", post, code)
        return
    await repo.retry_dispatched(post.post_id, next_at, code)
    await _log("INFO", "account_scheduled_post_deferred", post, code)


async def _resync_avatar(post: ScheduledPost) -> None:
    """Refresh the accounts-list avatar unless another photo is about to replace it."""
    soon = _now() + settings.scheduled_posts.avatar_resync_skip_seconds
    if await repo.photo_due_before(post.account_id, soon):
        return
    try:
        await refresh_account_avatar(post.account_id)
    except Exception:  # noqa: BLE001 - cosmetic; the post itself is already published
        logger.warning("avatar resync after scheduled photo failed for %s", post.account_id)


async def _settle(post: ScheduledPost, result: ActionResult) -> None:
    # A failed action can still have touched server state (the #249 pattern).
    invalidate_account_profile_cache(post.account_id)
    attempt = post.attempts + 1
    if result.status == "ok":
        story_id = result.message_id if post.kind == "story" else None
        # False: the row went with its account while the post was going out.
        if await repo.finish_dispatched(post.post_id, "done", story_id=story_id):
            # Resync first: the SPA refetches the accounts table on this event.
            if post.kind == "photo":
                await _resync_avatar(post)
            await _log("INFO", "account_scheduled_post_published", post)
    elif result.status in _TIMED_WAITS:
        seconds = result.flood_wait_seconds or settings.scheduled_posts.retry_base_seconds
        jitter = _rng.randint(0, _RETRY_JITTER_SECONDS)
        await _retry_or_fail(post, _now() + seconds + jitter, result.status)
    elif result.status == "unavailable" and result.error_type == UNCONFIRMED_ERROR_TYPE:
        await repo.finish_dispatched(post.post_id, "ambiguous", error_code="unconfirmed")
        await _log("WARNING", "account_scheduled_post_ambiguous", post, "unconfirmed")
    elif result.status == "unavailable":
        base = settings.scheduled_posts.retry_base_seconds
        delay = min(settings.scheduled_posts.retry_max_seconds, base * 2 ** (attempt - 1))
        await _retry_or_fail(post, _now() + delay, "unavailable")
    else:
        # Everything else is terminal: an unmapped refusal may have come after the
        # upload, so repeating it could publish twice.
        code = result_code(result)
        await repo.finish_dispatched(post.post_id, "failed", error_code=code)
        await _log("WARNING", "account_scheduled_post_failed", post, code)


async def _pace(post: ScheduledPost) -> None:
    low = settings.scheduled_posts.global_gap_min_seconds
    high = settings.scheduled_posts.global_gap_max_seconds
    await await_send_slot(_GLOBAL_PACING_KEY, _rng.uniform(low, high))
    await await_send_slot(
        f"scheduled:{post.account_id}", settings.scheduled_posts.account_gap_seconds
    )


async def _abandon_unsent(post: ScheduledPost) -> None:
    """An unexpected fault before the call: back off and let the queue retry it."""
    base = settings.scheduled_posts.retry_base_seconds
    await repo.release_unsent(post.post_id, _now() + base)


async def _abandon_sent(post: ScheduledPost) -> None:
    """A fault after the call started: it may be on Telegram, so never retry it.

    A compare-and-set from ``dispatching``: a no-op when the settle already landed.
    """
    with suppress(Exception):
        await repo.finish_dispatched(post.post_id, "ambiguous", error_code="unconfirmed")


async def publish(post: ScheduledPost, *, accepting: Callable[[], bool]) -> None:  # noqa: C901 - one branch per dispatch phase
    """Pace, then publish ``post`` under the account lifecycle lock, then settle it."""
    from services.warming import account_lock  # noqa: PLC0415 - avoids an import cycle

    # Set right before the Telegram call, not after ``mark_dispatching``: a cancel
    # that lands while that write commits in its thread has still sent nothing.
    sent = False
    settling: asyncio.Future[None] | None = None
    try:
        if await _refused_early(post):
            return
        await _pace(post)
        if not accepting():
            await repo.release_unsent(post.post_id)
            return
        async with account_lock(post.account_id):
            refusal = await _precheck(post)
            if refusal is not None:
                state, code = refusal
                if await repo.settle_queued(post.post_id, state, code):
                    await _log("WARNING", "account_scheduled_post_failed", post, code)
                return
            try:
                action = await _build_action(post)
            except scheduled_media.MediaStoreError as exc:
                if await repo.settle_queued(post.post_id, "failed", exc.code):
                    await _log("WARNING", "account_scheduled_post_failed", post, exc.code)
                return
            # Lost to a cancel / reschedule that arrived while this post waited.
            if not await repo.mark_dispatching(post.post_id):
                return
            sent = True
            result = await execute(post.account_id, action, domain=_DOMAIN)
        settling = asyncio.ensure_future(_settle(post, result))
        await asyncio.shield(settling)
    except asyncio.CancelledError:
        if settling is not None:
            with suppress(Exception):
                await settling
        elif sent:
            await asyncio.shield(_abandon_sent(post))
        else:
            await asyncio.shield(repo.release_unsent(post.post_id))
        raise
    except Exception as exc:
        logger.exception("scheduled post %s failed unexpectedly", post.post_id)
        # Anything this leaves unwritten is an orphaned claim the worker's next
        # recovery pass settles the same way.
        with suppress(Exception):
            await (_abandon_sent(post) if sent else _abandon_unsent(post))
        await log_event(
            "ERROR",
            "account_scheduled_post_worker_failed",
            account_id=post.account_id,
            extra={"error_type": type(exc).__name__},
        )
