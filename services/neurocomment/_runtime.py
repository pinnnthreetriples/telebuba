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
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.db import (
    get_listener_account_id,
    get_listener_running,
    list_active_watch_channels,
    list_campaigns,
    list_warming_account_ids,
    reclaim_stale_claims,
    set_listener_account_id,
    set_listener_running,
)
from core.logging import log_event
from core.telegram_client import stop_post_listener, subscribe_posts
from schemas.neurocomment import NeurocommentRuntimeStatus
from schemas.telegram_actions import JoinChannel
from services.neurocomment import _seams
from services.neurocomment.engine import handle_new_post
from services.neurocomment.onboarding import onboard_campaign

if TYPE_CHECKING:
    from collections.abc import Callable

    from schemas.neurocomment_progress import OnboardingProgressEvent
    from schemas.telegram_actions import NewPostEvent


class ListenerBusyWarmingError(Exception):
    """Raised when the picked listener account is currently warming.

    Warming and neurocomment are mutually exclusive per account (the rest of the
    codebase enforces this via ``promoted_to_nc``); the listener pick is the one
    path that bypassed it, so we reject it at save time.
    """


# In-flight on-post tasks, tracked so shutdown can cancel them.
# ponytail: single-process, in-memory. Bounded by
# ``settings.neurocomment.max_concurrent_post_tasks`` — posts arriving above the cap
# are dropped (logged) rather than spawning unbounded tasks under a flood.
_TASKS: set[asyncio.Task[None]] = set()

# The single periodic deletion sweep (#131), tracked so reconcile/shutdown can
# (re)start and cancel it. None when the runtime is stopped or the sweep disabled.
_SWEEP_TASK: asyncio.Task[None] | None = None

# The single in-flight campaign-onboarding task spawned by Start. Tracked so a rapid
# second Start does not spawn a duplicate, and so shutdown cancels it cleanly. None
# when no onboarding run is in flight.
_ONBOARD_TASK: asyncio.Task[None] | None = None


async def on_post(event: NewPostEvent) -> None:
    """Listener callback: spawn the pipeline task and return at once (non-blocking).

    Bounded: at capacity the post is dropped (logged), so a flood cannot spawn
    unbounded tasks. The len-check → create_task → add sequence stays await-free so
    it is atomic on the single event loop (no interleaving grows ``_TASKS`` past the cap).
    """
    if len(_TASKS) >= settings.neurocomment.max_concurrent_post_tasks:
        await log_event(
            "WARNING",
            "neurocomment_post_dropped_overloaded",
            extra={"channel": event.channel, "in_flight": len(_TASKS)},
        )
        return
    task = asyncio.create_task(handle_new_post(event))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def reconcile_neurocomment_runtime(listener_account_id: str) -> None:
    """(Re)point the listener at the current active watch set. Idempotent.

    No active channels → stop the listener (idempotent). Safe to call on every
    boot; ``subscribe_posts`` itself drops any prior handler before registering.
    """
    # Warming and neurocomment are mutually exclusive per account. This is the
    # single choke point every subscription path funnels through (start, channel
    # edit, startup resume), so the guard lives here — start_neurocomment adds an
    # early raise on top for the interactive 409. A warming listener is unsubscribed
    # (never re-subscribed) rather than raising, so boot/channel-edit stay safe.
    if listener_account_id in await list_warming_account_ids():
        await stop_post_listener(listener_account_id)
        await _stop_sweep()
        await log_event(
            "WARNING",
            "neurocomment_listener_warming_skipped",
            account_id=listener_account_id,
        )
        return
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
    await _stop_onboarding()
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


async def start_neurocomment(
    listener_account_id: str,
    *,
    on_progress: Callable[[OnboardingProgressEvent], None] | None = None,
) -> None:
    """Point the runtime at ``listener_account_id`` promptly; onboard in the background.

    Persisting the listener + reconciling are fast, so Start returns at once and the
    POST never blocks on the minutes of jittered join/challenge sleeps onboarding
    incurs. Onboarding for every active campaign runs as a tracked background task
    (progress is observable over the SSE log stream); shutdown cancels it cleanly.

    Switching the listener account: if a *different* account was the listener, stop its
    subscription first — ``subscribe_posts``/``stop_post_listener`` are keyed per
    account, so leaving the old one wired would have both accounts receive every post.

    A rapid second Start does not spawn a duplicate onboarding task while the first is
    still in flight; already-ready pairs are skipped inside onboarding regardless.
    """
    if listener_account_id in await list_warming_account_ids():
        raise ListenerBusyWarmingError(listener_account_id)
    previous = await get_listener_account_id()
    if previous is not None and previous != listener_account_id:
        await stop_post_listener(previous)
    await set_listener_account_id(listener_account_id)
    await set_listener_running(running=True)
    await reconcile_neurocomment_runtime(listener_account_id)
    _ensure_onboarding_running(on_progress)


def _ensure_onboarding_running(
    on_progress: Callable[[OnboardingProgressEvent], None] | None,
) -> None:
    """Spawn the background campaign-onboarding task unless one is already in flight."""
    global _ONBOARD_TASK  # noqa: PLW0603 - single module-level onboarding-task handle
    if _ONBOARD_TASK is not None and not _ONBOARD_TASK.done():
        return
    _ONBOARD_TASK = asyncio.create_task(_onboard_active_campaigns(on_progress))


async def _onboard_active_campaigns(
    on_progress: Callable[[OnboardingProgressEvent], None] | None,
) -> None:
    """Onboard every active campaign (background). One campaign's failure is isolated."""
    for campaign in (await list_campaigns()).campaigns:
        if campaign.status != "active":
            continue
        try:
            await onboard_campaign(campaign.campaign_id, on_progress=on_progress)
        except Exception as exc:  # noqa: BLE001 - one campaign must never abort onboarding
            await log_event(
                "ERROR",
                "neurocomment_start_onboard_failed",
                extra={
                    "campaign_id": campaign.campaign_id,
                    "error_type": type(exc).__name__,
                },
            )


async def stop_neurocomment() -> None:
    """PAUSE the runtime: unsubscribe but KEEP the remembered listener account.

    The operator's play/pause button lands here. ``listener_running`` is cleared
    even if shutdown raises, so a fresh boot never auto-resumes a paused listener
    (``reconcile_neurocomment_on_startup`` gates on it). ``listener_account_id`` is
    deliberately left intact so the strip survives a reload in the paused state —
    that is what distinguishes pause from "снять слушателя" (see
    :func:`clear_neurocomment_listener`).
    """
    listener_account_id = await get_listener_account_id()
    try:
        if listener_account_id is not None:
            await shutdown_neurocomment_runtime(listener_account_id)
    finally:
        await set_listener_running(running=False)


async def clear_neurocomment_listener() -> None:
    """REMOVE the listener ("снять слушателя"): unsubscribe and forget the account.

    Unlike :func:`stop_neurocomment` (pause), this clears ``listener_account_id`` as
    well as ``listener_running`` so the strip reverts to the "выберите аккаунт"
    placeholder. Both are cleared even if shutdown raises.
    """
    listener_account_id = await get_listener_account_id()
    try:
        if listener_account_id is not None:
            await shutdown_neurocomment_runtime(listener_account_id)
    finally:
        await set_listener_account_id(None)
        await set_listener_running(running=False)


async def neurocomment_runtime_status() -> NeurocommentRuntimeStatus:
    """Fleet runtime state for the UI: is the engine subscribed, and over how many channels.

    ``running`` reflects the persisted ``listener_running`` flag (actively
    subscribed), not merely whether an account is remembered. The remembered
    ``listener_account_id`` is always returned when one is set, so a *paused*
    runtime shows the listener strip with ``running=False`` — the SPA tells "paused
    with a remembered listener" from "no listener" by that field being non-null.
    The watch set is only read when running, so a paused/stopped engine costs two
    scalar reads.
    """
    log_limit = settings.neurocomment.log_limit
    listener_account_id = await get_listener_account_id()
    running = await get_listener_running()
    if not running:
        return NeurocommentRuntimeStatus(
            running=False,
            listener_account_id=listener_account_id,
            log_limit=log_limit,
        )
    channels = (await list_active_watch_channels()).channels
    return NeurocommentRuntimeStatus(
        running=True,
        active_channels=len(channels),
        listener_account_id=listener_account_id,
        log_limit=log_limit,
    )


async def reconcile_if_running() -> None:
    """Re-point the live listener at the current watch set — no-op when not running.

    Called after a channel link/unlink so the running listener's subscription tracks
    the DB immediately, instead of only at the next start/boot. Gated on
    ``listener_running`` so a *paused* runtime (id remembered, flag off) is not
    silently resubscribed by a channel edit.
    """
    if not await get_listener_running():
        return
    listener_account_id = await get_listener_account_id()
    if listener_account_id is not None:
        await reconcile_neurocomment_runtime(listener_account_id)


async def reconcile_neurocomment_on_startup() -> None:
    """No-arg ``app.on_startup`` hook: resume the listener only if it was running.

    A remembered-but-*paused* listener (``listener_account_id`` set,
    ``listener_running`` False) stays paused across a reboot — resuming it would
    silently re-enable a runtime the operator turned off (audit 2026-07-02).

    Stale claims are reclaimed unconditionally first: a crash mid-post leaves rows
    stuck ``claimed`` forever (the post_id is then permanently un-claimable), and
    that must be cleaned up even for a runtime that boots paused.
    """
    await _reclaim_stale_claims_on_startup()
    if not await get_listener_running():
        return
    listener_account_id = await get_listener_account_id()
    if listener_account_id is not None:
        await reconcile_neurocomment_runtime(listener_account_id)


async def _reclaim_stale_claims_on_startup() -> None:
    """Mark claims stuck 'claimed' since before the reclaim cutoff as 'failed'."""
    cutoff = (
        datetime.now(UTC) - timedelta(seconds=settings.neurocomment.stale_claim_reclaim_seconds)
    ).isoformat()
    reclaimed = await reclaim_stale_claims(cutoff)
    if reclaimed:
        await log_event("INFO", "neurocomment_stale_claims_reclaimed", extra={"count": reclaimed})


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


async def _stop_onboarding() -> None:
    """Cancel the background campaign-onboarding task (bounded wait), if in flight."""
    global _ONBOARD_TASK  # noqa: PLW0603 - single module-level onboarding-task handle
    task = _ONBOARD_TASK
    _ONBOARD_TASK = None
    if task is None or task.done():
        return
    task.cancel()
    with suppress(TimeoutError):
        await asyncio.wait_for(
            asyncio.gather(task, return_exceptions=True),
            timeout=settings.neurocomment.stop_cancel_timeout_seconds,
        )


def reset_for_tests() -> None:
    """Test-only reset; production code never calls this."""
    global _SWEEP_TASK, _ONBOARD_TASK  # noqa: PLW0603 - single module-level task handles
    _TASKS.clear()
    if _SWEEP_TASK is not None:  # pragma: no cover - tests await shutdown, so it's already None
        _SWEEP_TASK.cancel()
        _SWEEP_TASK = None
    if _ONBOARD_TASK is not None:
        _ONBOARD_TASK.cancel()
        _ONBOARD_TASK = None


# The deletion sweep's work lives in ``_sweep`` (file-size cap); the task handle and
# its start/stop stay above (this module's lifecycle owns reconcile/shutdown). Re-
# exported so ``_ensure_sweep_running`` finds ``_sweep_loop`` and ``_runtime._sweep_*``
# still resolves for tests.
from services.neurocomment._sweep import (  # noqa: E402, F401 - re-export after the module body.
    _sweep_channel,
    _sweep_loop,
    _sweep_once,
)
