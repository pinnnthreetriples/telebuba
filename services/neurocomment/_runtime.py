"""Neurocomment runtime — listener wiring, on-post task ownership, shutdown.

One dedicated account runs the standing post listener (issue #119 wires which
account from the UI/config). Each surfaced post is handled in its own fire-and-
forget :class:`asyncio.Task` so the Telethon listener loop is never blocked, and
the tasks are tracked so shutdown can cancel them. Mirrors
``services.warming._runtime`` task ownership + shutdown-with-timeout.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

from core.config import settings
from core.db import (
    get_listener_account_id,
    get_listener_running,  # noqa: F401 - re-exported for _watch ownership validation
    list_campaigns,
    list_warming_account_ids,
    set_listener_account_id,
    set_listener_running,
)
from core.logging import log_event
from core.telegram_client import (
    stop_post_listener,
    subscribe_posts,  # noqa: F401 - re-exported: read by _watch + patched by tests via _runtime.
    take_lost_access_channels,  # noqa: F401 - re-exported: read by _join + patched via _runtime.
)
from services.neurocomment import _signals
from services.neurocomment._onboarding_owner import generation_fence
from services.neurocomment.engine import handle_new_post  # noqa: F401 - patched/runtime seam
from services.neurocomment.onboarding import (
    _join_jitter_seconds,  # noqa: F401 - re-exported: read by _join + patched by tests via _runtime.
    onboard_campaign,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

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
# ``settings.neurocomment.max_concurrent_post_tasks``. Excess work remains pending
# in SQLite rather than being lost under a flood.
_TASKS: set[asyncio.Task[None]] = set()
_INBOX_ACCEPTING = False
_INBOX_DISPATCH_LOCK: asyncio.Lock | None = None
_INBOX_GENERATION = 0
_INBOX_RETRY_TASK: asyncio.Task[None] | None = None
_BACKFILL_TASK: asyncio.Task[None] | None = None
_BACKFILL_TIMER_TASK: asyncio.Task[None] | None = None
_BACKFILL_GENERATION = 0
_BACKFILL_AT: dict[tuple[str, str], float] = {}
_RUNTIME_GENERATION = 0
_RUNTIME_ACCOUNT_ID: str | None = None
_RUNTIME_OWNER_INITIALIZED = False
_RECONCILE_GENERATION = 0
_WORKER_GENERATION: ContextVar[int | None] = ContextVar("nc_worker_generation", default=None)
_RETIRED_TASKS: set[asyncio.Task[None]] = set()
_LIFECYCLE_LOCK: asyncio.Lock | None = None
_LIFECYCLE_OWNER: asyncio.Task[object] | None = None
_LIFECYCLE_DEPTH = 0


def _inbox_dispatch_lock() -> asyncio.Lock:
    global _INBOX_DISPATCH_LOCK  # noqa: PLW0603 - loop-bound and reset in tests
    if _INBOX_DISPATCH_LOCK is None:
        _INBOX_DISPATCH_LOCK = asyncio.Lock()
    return _INBOX_DISPATCH_LOCK


def _activate_runtime_owner(account_id: str) -> int:
    """Return the current generation, replacing stale ownership when necessary."""
    global _RUNTIME_ACCOUNT_ID, _RUNTIME_GENERATION, _RUNTIME_OWNER_INITIALIZED  # noqa: PLW0603
    _RUNTIME_OWNER_INITIALIZED = True
    if account_id != _RUNTIME_ACCOUNT_ID:
        _RUNTIME_GENERATION += 1
        _RUNTIME_ACCOUNT_ID = account_id
    return _RUNTIME_GENERATION


def _invalidate_runtime_owner(account_id: str | None = None) -> None:
    """Fence every background action belonging to the current owner."""
    global _RUNTIME_ACCOUNT_ID, _RUNTIME_GENERATION, _RUNTIME_OWNER_INITIALIZED  # noqa: PLW0603
    if account_id is not None and account_id != _RUNTIME_ACCOUNT_ID:
        return
    _RUNTIME_GENERATION += 1
    _RUNTIME_ACCOUNT_ID = None
    _RUNTIME_OWNER_INITIALIZED = True


def _runtime_owner_is_current(account_id: str, generation: int) -> bool:
    return account_id == _RUNTIME_ACCOUNT_ID and generation == _RUNTIME_GENERATION


def _reserve_reconcile() -> int:
    global _RECONCILE_GENERATION  # noqa: PLW0603
    _RECONCILE_GENERATION += 1
    return _RECONCILE_GENERATION


def _reconcile_owner_is_current(
    account_id: str,
    owner_generation: int,
    reconcile_generation: int,
) -> bool:
    return _runtime_owner_is_current(account_id, owner_generation) and (
        reconcile_generation == _RECONCILE_GENERATION
    )


def _worker_generation_is_current() -> bool:
    generation = _WORKER_GENERATION.get()
    # Direct engine calls (operator/tests) have no runtime token and keep their historical
    # behavior. Every durable inbox worker sets one, which fences a stopped generation.
    return generation is None or generation == _RUNTIME_GENERATION


def _retain_until_done(task: asyncio.Task[None]) -> None:
    """Keep ownership of a cancellation-resistant task until it truly exits."""
    _RETIRED_TASKS.add(task)
    task.add_done_callback(_RETIRED_TASKS.discard)


@asynccontextmanager
async def neurocomment_lifecycle() -> AsyncIterator[None]:
    """Re-entrant, process-wide ownership for start/stop/switch/reconcile/delete."""
    global _LIFECYCLE_LOCK, _LIFECYCLE_OWNER, _LIFECYCLE_DEPTH  # noqa: PLW0603
    task = asyncio.current_task()
    if task is not None and _LIFECYCLE_OWNER is task:
        _LIFECYCLE_DEPTH += 1
        try:
            yield
        finally:
            _LIFECYCLE_DEPTH -= 1
        return
    if _LIFECYCLE_LOCK is None:
        _LIFECYCLE_LOCK = asyncio.Lock()
    async with _LIFECYCLE_LOCK:
        _LIFECYCLE_OWNER = task
        _LIFECYCLE_DEPTH = 1
        try:
            yield
        finally:
            _LIFECYCLE_DEPTH = 0
            _LIFECYCLE_OWNER = None


# The single periodic deletion sweep (#131), tracked so reconcile/shutdown can
# (re)start and cancel it. None when the runtime is stopped or the sweep disabled.
_SWEEP_TASK: asyncio.Task[None] | None = None
_SWEEP_STOPPING_TASK: asyncio.Task[None] | None = None

# The single in-flight campaign-onboarding task spawned by Start. Tracked so a rapid
# second Start does not spawn a duplicate, and so shutdown cancels it cleanly. None
# when no onboarding run is in flight.
_ONBOARD_TASK: asyncio.Task[None] | None = None
_ONBOARD_TASK_OWNER: tuple[str, int] | None = None

# A trigger that arrives while onboarding is in flight queues exactly one rerun,
# so a channel/account added mid-run is picked up when the pass finishes instead
# of waiting for the next mutation. ponytail: one coalescing bool, not a queue.
_ONBOARD_RERUN = False

# The single in-flight paced channel-join task. Running the jittered per-channel joins
# inline blocked Start (under the per-account lock) and channel-edit requests for minutes,
# so it runs off the hot path. Single-flighted so concurrent reconciles never pace in
# parallel (bursting joins) and the cap check-then-record can't race across passes.
_JOIN_TASK: asyncio.Task[None] | None = None
_JOIN_TASK_OWNER: tuple[str, int] | None = None

# A trigger while a join pass is in flight queues one rerun, so channels linked mid-pace
# are joined by the coalesced rerun (which re-reads the watch set). Mirrors _ONBOARD_RERUN.
_JOIN_RERUN = False

# (listener, channel) pairs successfully joined this process, so reconcile does not
# re-join every channel on every call (10 rapid channel links = dozens of join RPCs
# before this guard — a real Telegram flood risk). Joins are idempotent, so this is
# a flood guard, not a correctness cache. ponytail: process-lifetime; a failed join
# simply retries on the next reconcile, and only a PROVEN access loss evicts an entry
# (see ``_join._mark_lost_channels``), and only while re-join attempts remain.
_JOINED_CHANNELS: set[tuple[str, str]] = set()

# Watch channels the listener could not resolve to a peer id: absent from the NewMessage
# filter, so no post from them EVER reaches the engine while the board still renders them
# ready. Refreshed on every reconcile so the status query surfaces the gap without a
# Telegram round-trip. ponytail: single-process, in-memory, like _JOINED_CHANNELS.
_UNWATCHED_CHANNELS: set[str] = set()


async def on_post(event: NewPostEvent) -> None:
    """Listener callback: durably enqueue before returning; never drop on overload."""
    await _inbox_runtime.on_post(event)


async def shutdown_neurocomment_runtime(listener_account_id: str) -> None:
    """Stop the listener + deletion sweep and cancel in-flight on-post tasks (bounded wait)."""
    _invalidate_runtime_owner(listener_account_id)
    await stop_post_listener(listener_account_id)
    await _inbox_runtime.stop_inbox()
    await _stop_sweep()
    await _stop_onboarding()
    await _stop_join()
    # Drop the gap report with the subscription it described: ``start_neurocomment`` sets
    # ``listener_running`` BEFORE it reconciles, so a poll landing in between would
    # otherwise be served the previous session's channel names.
    _publish_unwatched()
    tasks = list(_TASKS)
    await _cancel_bounded(*tasks)


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

    async with neurocomment_lifecycle(), account_lock(listener_account_id):
        if listener_account_id in await list_warming_account_ids():
            raise ListenerBusyWarmingError(listener_account_id)
        previous = await get_listener_account_id()
        if previous is not None and previous != listener_account_id:
            _invalidate_runtime_owner(previous)
            await stop_post_listener(previous)
        generation = _activate_runtime_owner(listener_account_id)
        await set_listener_account_id(listener_account_id)
        await set_listener_running(running=True)
    # Peer resolution is generation-fenced but deliberately outside the lifecycle lock,
    # so Stop/delete can invalidate a hung Start immediately.
    await reconcile_neurocomment_runtime(listener_account_id)
    async with neurocomment_lifecycle():
        if not _runtime_owner_is_current(listener_account_id, generation):
            return
        _ensure_onboarding_running(on_progress or _signals.signal_onboarding_progress)


def is_onboarding_running() -> bool:
    """True while the background campaign-onboarding pass is in flight."""
    return _ONBOARD_TASK is not None and not _ONBOARD_TASK.done()


def _ensure_onboarding_running(
    on_progress: Callable[[OnboardingProgressEvent], None] | None,
) -> None:
    """Spawn the onboarding task unless one is in flight; a mid-pass trigger queues one rerun."""
    global _ONBOARD_TASK, _ONBOARD_RERUN, _ONBOARD_TASK_OWNER  # noqa: PLW0603
    account_id = _RUNTIME_ACCOUNT_ID
    if account_id is None:
        return
    owner = (account_id, _RUNTIME_GENERATION)
    if _ONBOARD_TASK is not None and not _ONBOARD_TASK.done():
        if owner == _ONBOARD_TASK_OWNER:
            _ONBOARD_RERUN = True
            return
        _ONBOARD_TASK.cancel()
        _retain_until_done(_ONBOARD_TASK)
        _ONBOARD_RERUN = False
    _ONBOARD_TASK = asyncio.create_task(
        _onboard_active_campaigns(on_progress, account_id, _RUNTIME_GENERATION),
    )
    _ONBOARD_TASK_OWNER = owner


async def _onboard_active_campaigns(
    on_progress: Callable[[OnboardingProgressEvent], None] | None,
    owner_account_id: str,
    generation: int,
) -> None:
    """Onboard every active campaign (background); failures isolated, mid-pass reruns honored."""
    global _ONBOARD_RERUN  # noqa: PLW0603 - single module-level rerun flag
    while True:
        if not _runtime_owner_is_current(owner_account_id, generation):
            return
        for campaign in (await list_campaigns()).campaigns:
            if not _runtime_owner_is_current(owner_account_id, generation):
                return
            if campaign.status != "active":
                continue
            try:
                # Cancellation is the fast path. The task-local predicate also fences
                # a gateway/provider that catches cancellation and returns normally.
                with generation_fence(
                    lambda: _runtime_owner_is_current(owner_account_id, generation)
                ):
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


def _ensure_join_running(listener_account_id: str) -> None:
    """Spawn the paced join task unless one is in flight; a mid-pace trigger queues one rerun."""
    global _JOIN_TASK, _JOIN_RERUN, _JOIN_TASK_OWNER  # noqa: PLW0603
    generation = _activate_runtime_owner(listener_account_id)
    if _JOIN_TASK is not None and not _JOIN_TASK.done():
        if (listener_account_id, generation) == _JOIN_TASK_OWNER:
            _JOIN_RERUN = True
            return
        _JOIN_TASK.cancel()
        _retain_until_done(_JOIN_TASK)
    _JOIN_TASK = asyncio.create_task(_join_watch_channels(listener_account_id, generation))
    _JOIN_TASK_OWNER = (listener_account_id, generation)


async def _join_watch_channels(listener_account_id: str, generation: int) -> None:
    """Paced join task (background); reruns once if a channel was linked mid-pace.

    Single-flighted, so only one pacing stream runs at a time (no concurrent bursts)
    and the per-join cap check-then-record is serialized across passes. Its tail is the
    one place a channel that only became resolvable once joined gets back into the filter.
    """
    global _JOIN_RERUN  # noqa: PLW0603 - single module-level rerun flag
    while True:
        if not _runtime_owner_is_current(listener_account_id, generation):
            return
        await run_join_pass(listener_account_id, generation=generation)
        if not _runtime_owner_is_current(listener_account_id, generation):
            return
        if not _JOIN_RERUN:
            break
        _JOIN_RERUN = False
    # A channel unresolvable at subscribe time (not joined yet) is absent from the live
    # filter forever — nothing else reconciles. The joins are done now, so heal it here.
    if _runtime_owner_is_current(listener_account_id, generation):
        await _resubscribe_unwatched(listener_account_id)


async def _teardown_listener_locked(listener_account_id: str, *, clear_account: bool) -> None:
    """Tear down under the per-account lock; clear running (and the account when asked)."""
    from services.warming import account_lock  # noqa: PLC0415 - see start_neurocomment.

    async with neurocomment_lifecycle(), account_lock(listener_account_id):
        try:
            await shutdown_neurocomment_runtime(listener_account_id)
        finally:
            if clear_account:
                await set_listener_account_id(None)
            await set_listener_running(running=False)


async def stop_neurocomment() -> None:
    """PAUSE: unsubscribe but KEEP the remembered account (unlike clear, which forgets it)."""
    async with neurocomment_lifecycle():
        listener_account_id = await get_listener_account_id()
        if listener_account_id is None:
            _invalidate_runtime_owner()
            await set_listener_running(running=False)
            return
        await _teardown_listener_locked(listener_account_id, clear_account=False)


async def clear_neurocomment_listener() -> None:
    """REMOVE the listener ("снять слушателя"): unsubscribe and forget the account."""
    async with neurocomment_lifecycle():
        listener_account_id = await get_listener_account_id()
        if listener_account_id is None:
            _invalidate_runtime_owner()
            await set_listener_account_id(None)
            await set_listener_running(running=False)
            return
        await _teardown_listener_locked(listener_account_id, clear_account=True)


def _ensure_sweep_running() -> None:
    """Start the periodic deletion sweep if enabled and not already running."""
    global _SWEEP_TASK, _SWEEP_STOPPING_TASK  # noqa: PLW0603
    if settings.neurocomment.deletion_sweep_interval_seconds <= 0:
        return  # sweep disabled by config
    if _SWEEP_STOPPING_TASK is not None and not _SWEEP_STOPPING_TASK.done():
        return
    _SWEEP_STOPPING_TASK = None
    if _SWEEP_TASK is not None and not _SWEEP_TASK.done():
        return
    _SWEEP_TASK = asyncio.create_task(_sweep_loop())


async def _cancel_bounded(*tasks: asyncio.Task[None] | None) -> set[asyncio.Task[None]]:
    """Cancel the given tasks and wait a bounded time for them to unwind; ``None`` skipped.

    One body for all four cancel sites (sweep/onboarding/join + on-post tasks): they only
    differ in which handle they clear, and ``_runtime`` sits under the aislop size cap.

    The CURRENT task is skipped rather than cancelled, because a task cannot cancel itself
    and then await itself. A lifecycle rule running inside the sweep can drop the last watch
    channel; ``deactivate_channel`` reconciles the listener, which finds an empty watch set
    and stops the sweep — i.e. asks this very task to cancel itself. ``cancel()`` on the
    running task only arms ``_must_cancel``, and the ``gather`` below then suspends on a
    future whose only child is that same task: delivering the arm cancels the gather, which
    cancels its child, which re-enters the gather it is waiting on — unbounded recursion, a
    task wedged "cancelling" forever and uncancellable, and the caller (mid-drop, its reason
    not yet logged) never returning. The caller has already cleared the handle, and that is
    the stop signal a self-stopped loop reads: it drains the tick it is in and retires (see
    ``_sweep._sweep_loop``), which is what its caller wants anyway — the drop finishes and
    is logged, and the loop still ends promptly.
    """
    current = asyncio.current_task()
    live = [task for task in tasks if task is not None and task is not current]
    if not live:
        return set()
    for task in live:
        if not task.done():
            task.cancel()
    _done, pending = await asyncio.wait(
        live,
        timeout=settings.neurocomment.stop_cancel_timeout_seconds,
    )
    for task in pending:
        # A second cancellation finishes the common "caught once, then awaited again"
        # shape without extending the bounded wait. A task that suppresses every cancel
        # remains owned by ``_RETIRED_TASKS`` and generation-fenced.
        task.cancel()
        _retain_until_done(task)
    return pending


async def _stop_sweep() -> None:
    """Cancel the periodic deletion sweep (bounded wait), if running."""
    global _SWEEP_TASK, _SWEEP_STOPPING_TASK  # noqa: PLW0603
    task = _SWEEP_TASK
    if task is None:
        return
    # Move, do not clear ownership: active becomes None so a self-stopping sweep sees
    # the identity mismatch and retires at the end of its tick; the stopping slot blocks
    # replacement until even a cancellation-resistant task has actually quiesced.
    _SWEEP_TASK = None
    _SWEEP_STOPPING_TASK = task
    await _cancel_bounded(task)
    if task.done():
        if _SWEEP_STOPPING_TASK is task:
            _SWEEP_STOPPING_TASK = None
        return

    # A task that suppresses cancellation remains the sole sweep owner. Keeping the
    # handle blocks Start/reconcile from spawning an overlapping loop; the callback
    # releases ownership only once the old task has really quiesced.
    _retain_until_done(task)

    def _clear(completed: asyncio.Task[None]) -> None:
        global _SWEEP_STOPPING_TASK  # noqa: PLW0603
        if _SWEEP_STOPPING_TASK is completed:
            _SWEEP_STOPPING_TASK = None

    task.add_done_callback(_clear)


async def _stop_onboarding() -> None:
    """Cancel the background campaign-onboarding task (bounded wait), if in flight."""
    global _ONBOARD_TASK, _ONBOARD_RERUN, _ONBOARD_TASK_OWNER  # noqa: PLW0603
    _ONBOARD_RERUN = False  # shutdown discards any queued rerun
    task, _ONBOARD_TASK = _ONBOARD_TASK, None
    _ONBOARD_TASK_OWNER = None
    await _cancel_bounded(task)


async def _stop_join() -> None:
    """Cancel the background paced join task (bounded wait), if in flight."""
    global _JOIN_TASK, _JOIN_RERUN, _JOIN_TASK_OWNER  # noqa: PLW0603
    _JOIN_RERUN = False  # shutdown discards any queued rerun
    task, _JOIN_TASK = _JOIN_TASK, None
    _JOIN_TASK_OWNER = None
    await _cancel_bounded(task)


def reset_for_tests() -> None:  # noqa: PLR0915 - resets every owned runtime handle
    """Test-only reset; production code never calls this."""
    global _SWEEP_TASK, _SWEEP_STOPPING_TASK  # noqa: PLW0603
    global _ONBOARD_TASK, _ONBOARD_RERUN, _JOIN_TASK, _JOIN_RERUN  # noqa: PLW0603
    global _ONBOARD_TASK_OWNER  # noqa: PLW0603
    global _JOIN_TASK_OWNER  # noqa: PLW0603
    global _INBOX_DISPATCH_LOCK, _INBOX_ACCEPTING, _INBOX_GENERATION  # noqa: PLW0603
    global _INBOX_RETRY_TASK, _BACKFILL_TASK, _BACKFILL_GENERATION  # noqa: PLW0603
    global _BACKFILL_TIMER_TASK  # noqa: PLW0603
    global _LIFECYCLE_LOCK, _LIFECYCLE_OWNER, _LIFECYCLE_DEPTH  # noqa: PLW0603
    global _RUNTIME_ACCOUNT_ID, _RUNTIME_GENERATION, _RUNTIME_OWNER_INITIALIZED  # noqa: PLW0603
    global _RECONCILE_GENERATION  # noqa: PLW0603
    _TASKS.clear()
    _JOINED_CHANNELS.clear()
    _UNWATCHED_CHANNELS.clear()
    _ONBOARD_RERUN = False
    _ONBOARD_TASK_OWNER = None
    _JOIN_RERUN = False
    _JOIN_TASK_OWNER = None
    _INBOX_ACCEPTING = True
    _INBOX_DISPATCH_LOCK = None
    _INBOX_GENERATION = 0
    _BACKFILL_GENERATION = 0
    _BACKFILL_AT.clear()
    _LIFECYCLE_LOCK = None
    _LIFECYCLE_OWNER = None
    _LIFECYCLE_DEPTH = 0
    _RUNTIME_ACCOUNT_ID = None
    _RUNTIME_GENERATION = 0
    _RUNTIME_OWNER_INITIALIZED = False
    _RECONCILE_GENERATION = 0
    for task in tuple(_RETIRED_TASKS):
        task.cancel()
    _RETIRED_TASKS.clear()
    if _BACKFILL_TASK is not None:
        _BACKFILL_TASK.cancel()
        _BACKFILL_TASK = None
    if _BACKFILL_TIMER_TASK is not None:
        _BACKFILL_TIMER_TASK.cancel()
        _BACKFILL_TIMER_TASK = None
    if _INBOX_RETRY_TASK is not None:
        _INBOX_RETRY_TASK.cancel()
        _INBOX_RETRY_TASK = None
    if _SWEEP_TASK is not None:  # pragma: no cover - tests await shutdown, so it's already None
        _SWEEP_TASK.cancel()
        _SWEEP_TASK = None
    _SWEEP_STOPPING_TASK = None
    if _ONBOARD_TASK is not None:
        _ONBOARD_TASK.cancel()
        _ONBOARD_TASK = None
    if _JOIN_TASK is not None:
        _JOIN_TASK.cancel()
        _JOIN_TASK = None


# The paced join loop body lives in ``_join`` (file-size cap); the handle + start/stop
# stay above. Re-exported so ``_join_watch_channels`` finds ``run_join_pass``.
from services.neurocomment import _inbox_runtime  # noqa: E402 - peer runtime module
from services.neurocomment._join import run_join_pass  # noqa: E402 - re-export after body.

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

# Reconcile + the unwatched-channel report live in ``_watch`` (file-size cap); the
# ``_UNWATCHED_CHANNELS`` set they publish into stays above (tests read and seed it as
# ``_runtime._UNWATCHED_CHANNELS``). Re-exported so shutdown/the join tail find the
# helpers and ``_runtime.reconcile_neurocomment_runtime`` still resolves for callers.
from services.neurocomment._watch import (  # noqa: E402 - re-export after the module body.
    _publish_unwatched,
    _resubscribe_unwatched,
    reconcile_neurocomment_runtime,
)
