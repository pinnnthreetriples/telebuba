"""Neurocomment runtime — listener wiring, on-post task ownership, shutdown.

One dedicated account runs the standing post listener (issue #119 wires which
account from the UI/config). Each surfaced post is handled in its own fire-and-
forget :class:`asyncio.Task` so the Telethon listener loop is never blocked, and
the tasks are tracked so shutdown can cancel them. Mirrors
``services.warming._runtime`` task ownership + shutdown-with-timeout.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.db import (
    fetch_active_campaign_for_channel,
    get_listener_account_id,
    list_active_watch_channels,
    list_posted_comments_since,
    set_listener_account_id,
)
from core.logging import log_event
from core.telegram_client import stop_post_listener, subscribe_posts
from schemas.neurocomment import NeurocommentRuntimeStatus
from schemas.telegram_actions import CheckMessagesAlive, CheckMessagesAliveResult, JoinChannel
from services.neurocomment import _seams, _state
from services.neurocomment.engine import handle_new_post

if TYPE_CHECKING:
    from schemas.neurocomment import CommentRecord
    from schemas.telegram_actions import NewPostEvent

# In-flight on-post tasks, tracked so shutdown can cancel them.
# ponytail: single-process, in-memory. Unbounded per-event tasks — bound with a
# semaphore here if post volume ever grows large.
_TASKS: set[asyncio.Task[None]] = set()

# The single periodic deletion sweep (#131), tracked so reconcile/shutdown can
# (re)start and cancel it. None when the runtime is stopped or the sweep disabled.
_SWEEP_TASK: asyncio.Task[None] | None = None


async def on_post(event: NewPostEvent) -> None:
    """Listener callback: spawn the pipeline task and return at once (non-blocking)."""
    task = asyncio.create_task(handle_new_post(event))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def reconcile_neurocomment_runtime(listener_account_id: str) -> None:
    """(Re)point the listener at the current active watch set. Idempotent.

    No active channels → stop the listener (idempotent). Safe to call on every
    boot; ``subscribe_posts`` itself drops any prior handler before registering.
    """
    channels = (await list_active_watch_channels()).channels
    if not channels:
        await stop_post_listener(listener_account_id)
        await _stop_sweep()
        return
    # The listener account only receives NewMessage updates for channels it has
    # joined, so subscribe (join) it to each one first. Join is idempotent
    # (already-participant → ok); a per-channel failure is logged, not fatal.
    # ponytail: re-joins on every reconcile (one API call/channel even when already
    # a member); gate on a membership check if it ever flood-limits.
    for channel in channels:
        result = await _seams.execute(listener_account_id, JoinChannel(channel=channel))
        if result.status != "ok":
            await log_event(
                "WARNING",
                "neurocomment_listener_join_failed",
                account_id=listener_account_id,
                extra={"channel": channel, "status": result.status},
            )
    await subscribe_posts(listener_account_id, channels, on_post)
    _ensure_sweep_running()
    await log_event(
        "INFO",
        "neurocomment_runtime_reconciled",
        account_id=listener_account_id,
        extra={"channels": len(channels)},
    )


async def shutdown_neurocomment_runtime(listener_account_id: str) -> None:
    """Stop the listener + deletion sweep and cancel in-flight on-post tasks (bounded wait)."""
    await stop_post_listener(listener_account_id)
    await _stop_sweep()
    if not _TASKS:
        return
    tasks = list(_TASKS)
    _TASKS.clear()
    for task in tasks:
        if not task.done():
            task.cancel()
    with suppress(TimeoutError):
        await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=settings.neurocomment.stop_cancel_timeout_seconds,
        )


async def start_neurocomment(listener_account_id: str) -> None:
    """Persist the listener account and (re)point the runtime at the watch set."""
    await set_listener_account_id(listener_account_id)
    await reconcile_neurocomment_runtime(listener_account_id)


async def stop_neurocomment() -> None:
    """Stop the runtime for the persisted listener account and clear it.

    The persisted id is cleared even if shutdown raises, so "stop" reliably
    stops: a fresh boot must never resume a listener the operator turned off.
    """
    listener_account_id = await get_listener_account_id()
    try:
        if listener_account_id is not None:
            await shutdown_neurocomment_runtime(listener_account_id)
    finally:
        await set_listener_account_id(None)


async def neurocomment_runtime_status() -> NeurocommentRuntimeStatus:
    """Fleet runtime state for the UI: is the engine listening, and over how many channels.

    Running == a listener account id is persisted (the one piece of runtime state
    that survives a restart). The watch set is only read when running, so a stopped
    engine costs a single scalar read.
    """
    listener_account_id = await get_listener_account_id()
    if listener_account_id is None:
        return NeurocommentRuntimeStatus(running=False)
    channels = (await list_active_watch_channels()).channels
    return NeurocommentRuntimeStatus(
        running=True,
        active_channels=len(channels),
        listener_account_id=listener_account_id,
    )


async def reconcile_neurocomment_on_startup() -> None:
    """No-arg ``app.on_startup`` hook: resume the listener if one is persisted."""
    listener_account_id = await get_listener_account_id()
    if listener_account_id is not None:
        await reconcile_neurocomment_runtime(listener_account_id)


async def shutdown_neurocomment_on_shutdown() -> None:
    """No-arg ``app.on_shutdown`` hook: tear the listener + tasks down on exit."""
    listener_account_id = await get_listener_account_id()
    if listener_account_id is not None:
        await shutdown_neurocomment_runtime(listener_account_id)


def _ensure_sweep_running() -> None:
    """Start the periodic deletion sweep if enabled and not already running."""
    global _SWEEP_TASK  # noqa: PLW0603 - single module-level sweep-task handle
    if settings.neurocomment.deletion_sweep_interval_seconds <= 0:
        return  # sweep disabled by config
    if _SWEEP_TASK is None or _SWEEP_TASK.done():
        _SWEEP_TASK = asyncio.create_task(_sweep_loop())


async def _stop_sweep() -> None:
    """Cancel the periodic deletion sweep (bounded wait), if running."""
    global _SWEEP_TASK  # noqa: PLW0603 - single module-level sweep-task handle
    task = _SWEEP_TASK
    _SWEEP_TASK = None
    if task is None or task.done():
        return
    task.cancel()
    with suppress(TimeoutError):
        await asyncio.wait_for(
            asyncio.gather(task, return_exceptions=True),
            timeout=settings.neurocomment.stop_cancel_timeout_seconds,
        )


async def _sweep_loop() -> None:
    """Re-read recent comments on an interval; back off channels with mass deletions.

    The lone non-event loop in the runtime. A sweep fault is logged and the loop
    keeps going — it must never die (mirrors the listener-safe on-post pipeline).
    """
    interval = settings.neurocomment.deletion_sweep_interval_seconds
    while True:
        await asyncio.sleep(interval)
        try:
            await _sweep_once()
        except Exception as exc:  # noqa: BLE001 - a sweep fault must never kill the loop.
            await log_event(
                "WARNING",
                "neurocomment_sweep_failed",
                extra={"error_type": type(exc).__name__, "message": str(exc)},
            )


async def _sweep_once() -> None:
    """One deletion pass: per active channel, count vanished comments → back off."""
    now = datetime.now(UTC)
    since_iso = (
        now - timedelta(hours=settings.neurocomment.deletion_sweep_lookback_hours)
    ).isoformat()
    # Group watched channels by active campaign so each campaign's recent comments
    # are read once, then bucketed back per channel for the deletion check.
    by_campaign: dict[str, list[str]] = defaultdict(list)
    for channel in (await list_active_watch_channels()).channels:
        campaign = await fetch_active_campaign_for_channel(channel)
        if campaign is not None:
            by_campaign[campaign.campaign_id].append(channel)
    for campaign_id, channels in by_campaign.items():
        comments = (await list_posted_comments_since(campaign_id, since_iso)).comments
        buckets: dict[str, list[CommentRecord]] = defaultdict(list)
        for comment in comments:
            buckets[comment.channel].append(comment)
        for channel in channels:
            await _sweep_channel(channel, buckets.get(channel, []), now)


async def _sweep_channel(channel: str, comments: list[CommentRecord], now: datetime) -> None:
    """Re-read one channel's recent comments; trip its back-off if too many are gone."""
    if _state.channel_in_backoff(channel, now):
        # Already cooled — skip the read and don't re-escalate. The same vanished
        # comments stay in the lookback window for hours, so re-counting them every
        # sweep would walk the back-off to its cap from a single deletion episode;
        # escalation must advance only after a cooldown lapses and deletions persist.
        return
    msg_ids = [c.comment_msg_id for c in comments if c.comment_msg_id is not None]
    if not msg_ids:
        return
    nc = settings.neurocomment
    # ponytail: reads as one comment-author (a group member). If that account was
    # later kicked, get_messages may report all ids gone (false trip) or raise (handled
    # below); add a reader quorum / membership check only if the canary shows false trips.
    reader = comments[0].account_id
    try:
        result = await _seams.execute_read(
            reader,
            CheckMessagesAlive(channel=channel, message_ids=msg_ids),
        )
    except Exception as exc:  # noqa: BLE001 - one channel's read must not abort the sweep.
        await log_event(
            "WARNING",
            "neurocomment_sweep_read_failed",
            account_id=reader,
            extra={"channel": channel, "error_type": type(exc).__name__},
        )
        return
    if not isinstance(result, CheckMessagesAliveResult):  # pragma: no cover - typed gateway
        return
    missing = len(result.missing_ids)
    if missing < nc.channel_backoff_min_deletions:
        return
    seconds = _state.trip_channel_backoff(
        channel,
        now,
        base_seconds=nc.channel_backoff_base_seconds,
        max_seconds=nc.channel_backoff_max_seconds,
    )
    await log_event(
        "WARNING",
        "neurocomment_channel_backoff",
        extra={"channel": channel, "missing": missing, "cooldown_seconds": seconds},
    )


def reset_for_tests() -> None:
    """Test-only reset; production code never calls this."""
    global _SWEEP_TASK  # noqa: PLW0603 - single module-level sweep-task handle
    _TASKS.clear()
    if _SWEEP_TASK is not None:  # pragma: no cover - tests await shutdown, so it's already None
        _SWEEP_TASK.cancel()
        _SWEEP_TASK = None
