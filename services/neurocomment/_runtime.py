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
    list_campaigns,
    list_warming_account_ids,
    record_join,
    set_listener_account_id,
    set_listener_running,
)
from core.logging import log_event
from core.telegram_client import stop_post_listener, subscribe_posts
from schemas.telegram_actions import JoinChannel
from services.neurocomment import _seams, _signals
from services.neurocomment._generate import _COOLDOWN_STATUSES
from services.neurocomment.engine import handle_new_post
from services.neurocomment.onboarding import (
    _at_join_cap,
    _join_jitter_seconds,
    onboard_campaign,
)

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

# A trigger that arrives while onboarding is in flight queues exactly one rerun,
# so a channel/account added mid-run is picked up when the pass finishes instead
# of waiting for the next mutation. ponytail: one coalescing bool, not a queue.
_ONBOARD_RERUN = False

# (listener, channel) pairs successfully joined this process, so reconcile does not
# re-join every channel on every call (10 rapid channel links = dozens of join RPCs
# before this guard — a real Telegram flood risk). Joins are idempotent, so this is
# a flood guard, not a correctness cache. ponytail: process-lifetime, never
# invalidated — a failed join simply retries on the next reconcile.
_JOINED_CHANNELS: set[tuple[str, str]] = set()


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
    # Gated on ``_JOINED_CHANNELS`` so repeated reconciles (every channel link
    # re-runs this) cost one join per (account, channel) per process, not per call.
    # Jittered pause between *actual* joins (cache-hits skip it, no pause before
    # the first) so a large watch set never fires as one join burst — the freeze
    # vector. Mirrors the campaign-onboarding pacing.
    first_join = True
    for channel in channels:
        if (listener_account_id, channel) in _JOINED_CHANNELS:
            continue
        # Rolling-24h join cap (anti-freeze): once the single listener account hits
        # its cap, stop the burst — remaining channels retry on the next reconcile as
        # the window rolls. Checked before the sleep so a capped run wastes no wait.
        if await _at_join_cap(listener_account_id):
            await log_event(
                "WARNING",
                "neurocomment_join_daily_cap",
                account_id=listener_account_id,
                extra={"channel": channel},
            )
            break
        if not first_join:
            await asyncio.sleep(_join_jitter_seconds())
        first_join = False
        result = await _seams.execute(listener_account_id, JoinChannel(channel=channel))
        if result.status in {"ok", "already_participant"}:
            # Either way the account IS in the channel → cache it so we stop
            # re-joining. Only a real join counts against the rolling-24h cap;
            # an already-participant no-op (e.g. every channel on a restart) must
            # not, else the count pins near the cap and starves genuine joins.
            _JOINED_CHANNELS.add((listener_account_id, channel))
            if result.status == "ok":
                await record_join(listener_account_id)
            continue
        if result.status in _COOLDOWN_STATUSES:
            # Telegram is rate-limiting this account: stop the join burst now
            # rather than fire the next RPC and escalate a soft flood-wait into a
            # hard freeze. Unjoined channels retry on the next reconcile (only ok
            # joins are cached).
            await log_event(
                "WARNING",
                "neurocomment_listener_join_flood",
                account_id=listener_account_id,
                extra={"channel": channel, "status": result.status},
            )
            break
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

    Persisting the listener + reconciling are fast, so Start returns at once instead of
    blocking on onboarding's minutes of jittered join/challenge sleeps; onboarding runs
    as a tracked background task (progress on the SSE log stream, cancelled on shutdown).
    Switching accounts stops the previous account's subscription first (listeners are
    keyed per account); a rapid second Start won't spawn a duplicate onboarding task.

    The warming-check → flag-commit → reconcile all run under the shared per-account
    lifecycle lock (the one ``start_warming`` holds): a concurrent ``start_warming`` or
    ``stop_neurocomment`` can't interleave, so no orphan listener survives a pause. No
    deadlock — reconcile hits Telegram only via ``core.telegram_client``/``_seams``
    (which never take this lock) and onboarding is fire-and-forget, not awaited.
    """
    from services.warming import account_lock  # noqa: PLC0415 - avoid a services import cycle.

    async with account_lock(listener_account_id):
        if listener_account_id in await list_warming_account_ids():
            raise ListenerBusyWarmingError(listener_account_id)
        previous = await get_listener_account_id()
        if previous is not None and previous != listener_account_id:
            await stop_post_listener(previous)
        await set_listener_account_id(listener_account_id)
        await set_listener_running(running=True)
        await reconcile_neurocomment_runtime(listener_account_id)
    _ensure_onboarding_running(on_progress or _signals.signal_onboarding_progress)


def is_onboarding_running() -> bool:
    """True while the background campaign-onboarding pass is in flight."""
    return _ONBOARD_TASK is not None and not _ONBOARD_TASK.done()


def _ensure_onboarding_running(
    on_progress: Callable[[OnboardingProgressEvent], None] | None,
) -> None:
    """Spawn the onboarding task unless one is in flight; a mid-pass trigger queues one rerun."""
    global _ONBOARD_TASK, _ONBOARD_RERUN  # noqa: PLW0603 - single module-level handles
    if _ONBOARD_TASK is not None and not _ONBOARD_TASK.done():
        _ONBOARD_RERUN = True
        return
    _ONBOARD_TASK = asyncio.create_task(_onboard_active_campaigns(on_progress))


async def _onboard_active_campaigns(
    on_progress: Callable[[OnboardingProgressEvent], None] | None,
) -> None:
    """Onboard every active campaign (background); failures isolated, mid-pass reruns honored."""
    global _ONBOARD_RERUN  # noqa: PLW0603 - single module-level rerun flag
    while True:
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
        if not _ONBOARD_RERUN:
            return
        _ONBOARD_RERUN = False


async def _teardown_listener_locked(listener_account_id: str, *, clear_account: bool) -> None:
    """Tear down under the per-account lock; clear running (and the account when asked)."""
    from services.warming import account_lock  # noqa: PLC0415 - see start_neurocomment.

    async with account_lock(listener_account_id):
        try:
            await shutdown_neurocomment_runtime(listener_account_id)
        finally:
            if clear_account:
                await set_listener_account_id(None)
            await set_listener_running(running=False)


async def stop_neurocomment() -> None:
    """PAUSE: unsubscribe but KEEP the remembered account (unlike clear, which forgets it)."""
    listener_account_id = await get_listener_account_id()
    if listener_account_id is None:
        await set_listener_running(running=False)
        return
    await _teardown_listener_locked(listener_account_id, clear_account=False)


async def clear_neurocomment_listener() -> None:
    """REMOVE the listener ("снять слушателя"): unsubscribe and forget the account."""
    listener_account_id = await get_listener_account_id()
    if listener_account_id is None:
        await set_listener_account_id(None)
        await set_listener_running(running=False)
        return
    await _teardown_listener_locked(listener_account_id, clear_account=True)


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
    global _ONBOARD_TASK, _ONBOARD_RERUN  # noqa: PLW0603 - single module-level handles
    _ONBOARD_RERUN = False  # shutdown discards any queued rerun
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
    global _SWEEP_TASK, _ONBOARD_TASK, _ONBOARD_RERUN  # noqa: PLW0603 - module-level handles
    _TASKS.clear()
    _JOINED_CHANNELS.clear()
    _ONBOARD_RERUN = False
    if _SWEEP_TASK is not None:  # pragma: no cover - tests await shutdown, so it's already None
        _SWEEP_TASK.cancel()
        _SWEEP_TASK = None
    if _ONBOARD_TASK is not None:
        _ONBOARD_TASK.cancel()
        _ONBOARD_TASK = None


# The app-lifecycle hooks + reconcile trigger + UI status query live in ``_lifecycle``
# (file-size cap); they call back into this module's core machinery. Re-exported so
# ``_runtime.<name>`` and the ``services.neurocomment`` package exports still resolve.
from services.neurocomment._lifecycle import (  # noqa: E402, F401 - re-export after the module body.
    _reclaim_stale_claims_on_startup,
    neurocomment_runtime_status,
    reconcile_if_running,
    reconcile_neurocomment_on_startup,
    shutdown_neurocomment_on_shutdown,
)

# The deletion sweep's work lives in ``_sweep`` (file-size cap); the task handle and
# its start/stop stay above (this module's lifecycle owns reconcile/shutdown). Re-
# exported so ``_ensure_sweep_running`` finds ``_sweep_loop`` and ``_runtime._sweep_*``
# still resolves for tests.
from services.neurocomment._sweep import (  # noqa: E402, F401 - re-export after the module body.
    _sweep_channel,
    _sweep_loop,
    _sweep_once,
)
