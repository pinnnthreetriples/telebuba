"""Neurocomment runtime — listener wiring, on-post task ownership, shutdown.

One dedicated account runs the standing post listener (issue #119 wires which
account from the UI/config). Each surfaced post is handled in its own fire-and-
forget :class:`asyncio.Task` so the Telethon listener loop is never blocked, and
the tasks are tracked so shutdown can cancel them. Mirrors
``services.warming._runtime`` task ownership + shutdown-with-timeout.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING

from core.config import settings
from core.db import (
    get_listener_account_id,
    list_active_watch_channels,
    set_listener_account_id,
)
from core.logging import log_event
from core.telegram_client import stop_post_listener, subscribe_posts
from schemas.telegram_actions import JoinChannel
from services.neurocomment import _seams
from services.neurocomment.engine import handle_new_post

if TYPE_CHECKING:
    from schemas.telegram_actions import NewPostEvent

# In-flight on-post tasks, tracked so shutdown can cancel them.
# ponytail: single-process, in-memory. Unbounded per-event tasks — bound with a
# semaphore here if post volume ever grows large.
_TASKS: set[asyncio.Task[None]] = set()


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
    await log_event(
        "INFO",
        "neurocomment_runtime_reconciled",
        account_id=listener_account_id,
        extra={"channels": len(channels)},
    )


async def shutdown_neurocomment_runtime(listener_account_id: str) -> None:
    """Stop the listener and cancel any in-flight on-post tasks (bounded wait)."""
    await stop_post_listener(listener_account_id)
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


def reset_for_tests() -> None:
    """Test-only reset; production code never calls this."""
    _TASKS.clear()
