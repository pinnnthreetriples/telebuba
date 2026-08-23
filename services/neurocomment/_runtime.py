"""Neurocomment listener ownership, durable dispatch, and bounded shutdown."""

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
)
from core.logging import log_event
from core.telegram_client import (
    stop_post_listener,
    subscribe_posts,  # noqa: F401 - re-exported: read by _watch + patched by tests via _runtime.
    take_lost_access_channels,  # noqa: F401 - re-exported: read by _join + patched via _runtime.
)
from services.neurocomment.engine import handle_new_post
from services.neurocomment.onboarding import (
    _join_jitter_seconds,  # noqa: F401 - re-exported: read by _join + patched by tests via _runtime.
    onboard_campaign,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from schemas.logs import LogLevel
    from schemas.neurocomment import CampaignList
    from schemas.neurocomment_pipeline import PipelineOutcome
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
_INBOX_ACCEPTING: bool = False
_INBOX_DISPATCH_LOCK: asyncio.Lock | None = None
_INBOX_GENERATION = 0
_INBOX_RETRY_TASK: asyncio.Task[None] | None = None
_BACKFILL_TASK: asyncio.Task[None] | None = None
_BACKFILL_TIMER_TASK: asyncio.Task[None] | None = None
_BACKFILL_GENERATION = 0
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
# Owner that asked for a sweep while a cancellation-resistant predecessor was
# still retiring.  ``_stop_sweep`` deliberately clears this: a genuine stop
# (empty watch set, warming listener, operator Stop) is not a restart request.
_SWEEP_RESTART_OWNER: tuple[str, int] | None = None

# The single in-flight campaign-onboarding task spawned by Start. Tracked so a rapid
# second Start does not spawn a duplicate, and so shutdown cancels it cleanly. None
# when no onboarding run is in flight.
_ONBOARD_TASK: asyncio.Task[None] | None = None
_ONBOARD_TASK_OWNER: tuple[str, int] | None = None

# A trigger that arrives while onboarding is in flight queues exactly one rerun,
# so a channel/account added mid-run is picked up when the pass finishes instead
# of waiting for the next mutation. ponytail: one coalescing bool, not a queue.
_ONBOARD_RERUN: bool = False

# The single in-flight paced channel-join task. Running the jittered per-channel joins
# inline blocked Start (under the per-account lock) and channel-edit requests for minutes,
# so it runs off the hot path. Single-flighted so concurrent reconciles never pace in
# parallel (bursting joins) and the cap check-then-record can't race across passes.
_JOIN_TASK: asyncio.Task[None] | None = None
_JOIN_TASK_OWNER: tuple[str, int] | None = None

# A trigger while a join pass is in flight queues one rerun, so channels linked mid-pace
# are joined by the coalesced rerun (which re-reads the watch set). Mirrors _ONBOARD_RERUN.
_JOIN_RERUN: bool = False

# Process-local successful joins prevent duplicate join bursts during reconciliation.
_JOINED_CHANNELS: set[tuple[str, str]] = set()

# Watch channels the listener could not resolve to a peer id: absent from the NewMessage
# filter, so no post from them EVER reaches the engine while the board still renders them
# ready. Refreshed on every reconcile so the status query surfaces the gap without a
# Telegram round-trip. ponytail: single-process, in-memory, like _JOINED_CHANNELS.
_UNWATCHED_CHANNELS: set[str] = set()


async def on_post(event: NewPostEvent) -> None:
    """Listener callback: durably enqueue before returning; never drop on overload."""
    await _inbox_runtime.on_post(event)


async def _handle_inbox_post(event: NewPostEvent) -> PipelineOutcome:
    """Call the patchable engine seam while making its runtime ownership explicit."""
    return await handle_new_post(event)


async def _stop_post_listener(account_id: str) -> None:
    await stop_post_listener(account_id)


async def _list_warming_account_ids() -> set[str]:
    return await list_warming_account_ids()


async def _runtime_get_listener_account_id() -> str | None:
    return await get_listener_account_id()


async def _list_campaigns() -> CampaignList:
    return await list_campaigns()


async def _onboard_campaign(
    campaign_id: str,
    *,
    on_progress: Callable[[OnboardingProgressEvent], None] | None = None,
) -> None:
    await onboard_campaign(campaign_id, on_progress=on_progress)


async def _runtime_log_event(
    level: LogLevel,
    event: str,
    account_id: str | None = None,
    extra: dict[str, object] | None = None,
) -> None:
    await log_event(level, event, account_id=account_id, extra=extra)


def _ensure_sweep_running() -> None:
    """Start the periodic deletion sweep if enabled and not already running."""
    global _SWEEP_RESTART_OWNER, _SWEEP_TASK, _SWEEP_STOPPING_TASK  # noqa: PLW0603
    if settings.neurocomment.deletion_sweep_interval_seconds <= 0:
        return  # sweep disabled by config
    account_id = _RUNTIME_ACCOUNT_ID
    generation = _RUNTIME_GENERATION
    if account_id is None:
        return
    if _SWEEP_STOPPING_TASK is not None and not _SWEEP_STOPPING_TASK.done():
        # Coalesce any number of Start/reconcile calls into one owner-scoped
        # replacement.  The predecessor's done callback consumes this marker.
        _SWEEP_RESTART_OWNER = (account_id, generation)
        return
    _SWEEP_STOPPING_TASK = None
    _SWEEP_RESTART_OWNER = None
    if _SWEEP_TASK is not None and not _SWEEP_TASK.done():
        return
    _SWEEP_TASK = asyncio.create_task(_run_owned_sweep(account_id, generation))


async def _run_owned_sweep(account_id: str, generation: int) -> None:
    """Run the sweep with a lease inherited by every Telegram/LLM seam it calls."""
    from services.neurocomment import _seams  # noqa: PLC0415

    with _seams.generation_scope(lambda: _runtime_owner_is_current(account_id, generation)):
        await _sweep_loop()


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
    global _SWEEP_RESTART_OWNER, _SWEEP_TASK, _SWEEP_STOPPING_TASK  # noqa: PLW0603
    # Stop is authoritative.  A later Start/reconcile may arm a replacement by
    # calling ``_ensure_sweep_running`` while the old task is still retiring.
    _SWEEP_RESTART_OWNER = None
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
        global _SWEEP_RESTART_OWNER, _SWEEP_STOPPING_TASK  # noqa: PLW0603
        if _SWEEP_STOPPING_TASK is completed:
            _SWEEP_STOPPING_TASK = None
            requested = _SWEEP_RESTART_OWNER
            _SWEEP_RESTART_OWNER = None
            # A Start/reconcile may have arrived while this stubborn task was still
            # retiring. Recover exactly that owner once quiescent. A self-stop never
            # arms the marker, and an owner switch makes a stale request a no-op.
            if requested == (_RUNTIME_ACCOUNT_ID, _RUNTIME_GENERATION):
                _ensure_sweep_running()

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


# Late imports preserve the runtime facade while avoiding peer-module cycles.
from services.neurocomment import _inbox_runtime  # noqa: E402 - peer runtime module
from services.neurocomment import _runtime_test_reset as _test_reset  # noqa: E402
from services.neurocomment._join import run_join_pass  # noqa: E402

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
from services.neurocomment._runtime_operations import (  # noqa: E402, F401 - compatibility facade
    _ensure_join_running,
    _ensure_onboarding_running,
    _join_watch_channels,
    _onboard_active_campaigns,
    _teardown_listener_locked,
    clear_neurocomment_listener,
    is_onboarding_running,
    remember_neurocomment_listener,
    shutdown_neurocomment_runtime,
    start_neurocomment,
    stop_neurocomment,
)

reset_for_tests = _test_reset.reset_for_tests
reset_for_tests_async = _test_reset.reset_for_tests_async

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
from services.neurocomment._watch import (  # noqa: E402
    _publish_unwatched,
    _resubscribe_unwatched,
    reconcile_neurocomment_runtime,
)


async def _reconcile_runtime(account_id: str) -> None:
    await reconcile_neurocomment_runtime(account_id)


def _publish_unwatched_runtime() -> None:
    _publish_unwatched()


async def _run_join_pass(account_id: str, *, generation: int) -> None:
    await run_join_pass(account_id, generation=generation)


async def _resubscribe_runtime(account_id: str) -> None:
    await _resubscribe_unwatched(account_id)
