"""Warming runtime — per-account loop tasks, start/stop, and the loop step.

Warming is a continuous randomised loop per account (cycle -> 12-30h sleep ->
repeat), so each running account owns an :class:`asyncio.Task` in ``_RUNTIME``.
``run_loop_iteration`` is the testable step; ``_warming_loop`` is the wrapper.

Telegram / Gemini / spam-probe / randomness are reached via
:mod:`services.warming._seams`; ``_in_quiet_hours`` is re-exported here so tests
patch it on this module.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import suppress
from typing import TYPE_CHECKING, NamedTuple

from core.config import settings
from core.db import (
    fetch_account,
    fetch_warming_state,
    get_listener_account_id,
    get_listener_running,
    get_spam_status,
    list_warming_channels,
    list_warming_states,
    load_warming_settings,
    unmark_promoted_to_nc,
)
from core.logging import log_event
from schemas.warming import (
    is_warming,
)
from services.dialogues import assign_pairs
from services.trust import account_trust_score
from services.warming import _seams
from services.warming._purge import purge_stale_history
from services.warming._runner import _warming_loop
from services.warming._state import _current_card, _set_state
from services.warming.pacing import (
    _now_iso,
    _proxy_snapshot,
    evaluate_readiness,
)

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.warming import (
        ActivityPersona,
        StartWarmingRequest,
        WarmingAccountState,
        WarmingReadiness,
        WarmingStateRecord,
    )

# Stdlib sink for full third-party text — see ``core.proxy_check._failed_result``.
logger = logging.getLogger(__name__)

# account_id -> running warming loop. Genuine runtime state (rare exception to
# the "no classes for stateless logic" rule): the loops must outlive a single
# UI handler call so the board can start/stop them.
_RUNTIME: dict[str, asyncio.Task[None]] = {}

# A revoked generation gets a distinct persisted marker. It fences every stale
# run_id CAS immediately and lets the task's done callback settle only that same
# timed-out stop to idle without racing a later Start.
_STOPPING_MARKERS: dict[asyncio.Task[None], tuple[str, str]] = {}
_STOP_FINALIZERS: set[asyncio.Task[None]] = set()

# Per-account async lock: prevents concurrent start/stop interleaving from
# leaving the DB and ``_RUNTIME`` in mismatched states. Locks are created lazily
# and never freed — the dictionary is bounded by the number of accounts.
_ACCOUNT_LOCKS: dict[str, asyncio.Lock] = {}

# Single background retention sweep, started with the runtime and cancelled on
# shutdown. A startup-only purge lets the append-only tables grow unbounded
# during long uptimes; this reruns it every ``warming.purge_interval_hours``.
_PURGE_TASK: asyncio.Task[None] | None = None


class UnknownAccountError(ValueError):
    """Raised when start/stop is called for an account that does not exist."""


class WarmingNotReadyError(ValueError):
    """Raised when ``start_warming`` refuses a not-ready account.

    Carries the structured ``reasons`` so the UI can show them to the user.
    """

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        super().__init__("; ".join(reasons) or "account not ready")


class AccountIsListenerError(ValueError):
    """Raised when ``start_warming`` refuses the running neurocomment listener.

    The reciprocal of neurocomment's ``ListenerBusyWarmingError``: the two runtimes
    are mutually exclusive per account, so an account cannot be warmed while it is
    the active listener.
    """


class WarmingTaskNotQuiescentError(RuntimeError):
    """The previous warming coroutine is still alive after bounded cancellation."""


def _account_lock(account_id: str) -> asyncio.Lock:
    lock = _ACCOUNT_LOCKS.get(account_id)
    if lock is None:
        lock = asyncio.Lock()
        _ACCOUNT_LOCKS[account_id] = lock
    return lock


def _discard_runtime_task(account_id: str, task: asyncio.Task[None]) -> None:
    """Release ownership only when ``task`` is still the registered task."""
    if _RUNTIME.get(account_id) is task:
        _RUNTIME.pop(account_id, None)


async def _settle_late_stop(account_id: str, stopping_marker: str) -> None:
    """Turn one timed-out stop idle iff its marker still owns the persisted row."""
    async with _account_lock(account_id):
        current = await fetch_warming_state(account_id)
        if current is None or current.run_id != stopping_marker:
            return
        await _set_state(
            account_id,
            "idle",
            last_event="stopped_after_timeout",
            last_error=None,
            stopped_at=_now_iso(),
            run_id=None,
            expected_run_id=stopping_marker,
        )


def _runtime_task_done(account_id: str, run_id: str, task: asyncio.Task[None]) -> None:
    """Done callback: terminal tasks leave both ownership registries atomically."""
    _discard_runtime_task(account_id, task)
    _seams.revoke_lease(account_id, run_id)


def _late_stopped_task_done(task: asyncio.Task[None]) -> None:
    """Schedule the CAS settle registered only after a bounded wait timed out."""
    stopped = _STOPPING_MARKERS.pop(task, None)
    if stopped is not None:
        stopped_account_id, marker = stopped
        finalizer = asyncio.create_task(_settle_late_stop(stopped_account_id, marker))
        _STOP_FINALIZERS.add(finalizer)
        finalizer.add_done_callback(_STOP_FINALIZERS.discard)


def _spawn_runtime_task(account_id: str, run_id: str) -> asyncio.Task[None]:
    """Create and register the sole task/lease pair for one account generation."""
    _seams.activate_lease(account_id, run_id)
    task = asyncio.create_task(_warming_loop(account_id, run_id=run_id))
    _RUNTIME[account_id] = task
    task.add_done_callback(
        lambda completed, aid=account_id, rid=run_id: _runtime_task_done(aid, rid, completed)
    )
    return task


async def _await_runtime_task(account_id: str, task: asyncio.Task[None]) -> bool:
    """Wait at most the configured budget; retain every non-terminal task."""
    if task.done():
        _discard_runtime_task(account_id, task)
        return True
    done, _pending = await asyncio.wait(
        {task},
        timeout=settings.warming.stop_cancel_timeout_seconds,
    )
    if not done:
        return False
    _discard_runtime_task(account_id, task)
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.exception("warming task failed while stopping %s", account_id)
        await log_event(
            "WARNING",
            "warming_stop_task_error",
            account_id=account_id,
            extra={"error_type": type(exc).__name__},
        )
    return True


async def _cancel_runtime_task(account_id: str, *, last_event: str) -> bool:
    """Revoke, cancel and bounded-wait without losing a cancellation-suppressing task."""
    task = _RUNTIME.get(account_id)
    if task is None or task.done():
        if task is not None:
            _discard_runtime_task(account_id, task)
        _seams.revoke_lease(account_id)
        return True

    # Revoke before cancel: even if the task catches CancelledError, the seam's
    # pre/post-dispatch fence prevents another Telegram action from this point.
    _seams.revoke_lease(account_id)
    stopping_marker = f"stopping-{uuid.uuid4().hex}"
    await _set_state(
        account_id,
        "error",
        last_event=last_event,
        last_error="warming task is stopping",
        heartbeat_at=_now_iso(),
        run_id=stopping_marker,
    )
    task.cancel()
    quiescent = await _await_runtime_task(account_id, task)
    if not quiescent:
        _STOPPING_MARKERS[task] = (account_id, stopping_marker)
        # add_done_callback also schedules the callback when the task completed
        # in the narrow window after asyncio.wait timed out.
        task.add_done_callback(_late_stopped_task_done)
    return quiescent


def assert_runtime_quiescent(account_id: str) -> None:
    """Refuse lifecycle hand-offs while an old warming coroutine still exists."""
    task = _RUNTIME.get(account_id)
    if task is not None and not task.done():
        raise WarmingTaskNotQuiescentError(account_id)


class _CarriedStint(NamedTuple):
    """The started_at / target_days / activity_persona a start should apply.

    П7: a restart-while-warming carries the original stint anchor, operator
    target, and persona; a genuine (re)start from idle/stopped restamps them.
    """

    started_at: str
    target_days: int
    activity_persona: ActivityPersona


def _carry_or_restamp(
    existing: WarmingStateRecord | None, data: StartWarmingRequest
) -> _CarriedStint:
    """Decide the stint fields once: carry the in-flight ones, restamp the rest."""
    started_at = (
        existing.started_at
        if existing and existing.started_at and is_warming(existing.state)
        else _now_iso()
    )
    target_days = (
        existing.target_days
        if existing is not None and existing.target_days and is_warming(existing.state)
        else (data.target_days or settings.neurocomment.warmed_min_days)
    )
    activity_persona = (
        existing.activity_persona
        if existing is not None and is_warming(existing.state)
        else data.activity_persona
    )
    return _CarriedStint(started_at, target_days, activity_persona)


async def _evaluate_account_readiness(
    account_id: str,
    account: AccountRead,
    channel_count: int,
) -> WarmingReadiness:
    """Last-known-state readiness verdict, fetching the account's spam + trust."""
    return evaluate_readiness(
        account,
        channel_count,
        spam=await get_spam_status(account_id),
        trust_score=await account_trust_score(account_id),
    )


async def _enforce_start_readiness(account_id: str, account: AccountRead) -> None:
    """Refuse a not-ready account at start, raising ``WarmingNotReadyError``."""
    if not (await load_warming_settings()).enforce_readiness:
        return
    channel_count = len((await list_warming_channels()).channels)
    readiness = await _evaluate_account_readiness(account_id, account, channel_count)
    if readiness.ready:
        return
    await log_event(
        "WARNING",
        "warming_start_blocked",
        account_id=account_id,
        extra={"reasons": readiness.reasons},
    )
    raise WarmingNotReadyError(readiness.reasons)


async def _cancel_existing_task(account_id: str) -> None:
    """Cancel + await any in-flight loop task so a "start now" isn't blocked by it.

    F2: the task may still be inside the inter-cycle
    ``asyncio.sleep(_loop_sleep_seconds(...))`` from the *previous* ``next_run_at``.
    Clearing that schedule cannot wake a sleeping task, so cancel-and-replace is
    the only way to honour the operator's "start now".
    """
    if await _cancel_runtime_task(account_id, last_event="restart_stopping"):
        return
    await log_event(
        "WARNING",
        "warming_restart_timeout",
        account_id=account_id,
    )
    raise WarmingNotReadyError(["previous warming task is still stopping"])


async def start_warming(data: StartWarmingRequest) -> WarmingAccountState:
    """Move an account into the warming column and kick off its loop task."""
    async with _account_lock(data.account_id):
        account = await fetch_account(data.account_id)
        if account is None:
            msg = f"Unknown account: {data.account_id}"
            raise UnknownAccountError(msg)
        # Reciprocal of the neurocomment listener guard: refuse to warm the account
        # that is the active listener, so the two runtimes never share a session.
        if await get_listener_running() and await get_listener_account_id() == data.account_id:
            raise AccountIsListenerError(data.account_id)
        await _enforce_start_readiness(data.account_id, account)
        # Revoke + cancel first, and publish a fresh generation only after the old
        # coroutine is terminal. A task that suppresses cancellation remains owned
        # and makes Start fail instead of overlapping Telegram activity. The
        # per-booking reservation token still lets normal cancellation hand unused
        # budget back before this new generation is minted.
        existing = await fetch_warming_state(data.account_id)
        await _cancel_existing_task(data.account_id)
        # P1.2: stamp a fresh generation marker so an in-flight cycle from
        # the previous run can detect and refuse to write through.
        run_id = uuid.uuid4().hex
        stint = _carry_or_restamp(existing, data)
        # Bug 2: a previously-promoted account dragged back into warming would
        # otherwise live in both pools — clear the flag so neurocomment's
        # warmed-account overview drops it on the next poll.
        if existing is not None and existing.promoted_to_nc:
            await unmark_promoted_to_nc(data.account_id)
            await log_event(
                "INFO",
                "warming_unpromoted_on_restart",
                account_id=data.account_id,
            )
        try:
            await _set_state(
                data.account_id,
                "active",
                last_event="queued",
                next_run_at=None,
                started_at=stint.started_at,
                stopped_at=None,
                last_error=None,
                # П6: clear the previous run's furthest-step/channel so the just-
                # queued card shows "online", not a stale send_dm/react on an old
                # channel until the first cycle write lands.
                last_action=None,
                last_channel=None,
                flood_wait_seconds=None,
                flood_wait_until=None,
                proxy_snapshot=_proxy_snapshot(account),
                run_id=run_id,
                target_days=stint.target_days,
                activity_persona=stint.activity_persona,
            )
            # Build dialogue ownership before the loop can run its first cycle.
            # Cancellation in this awaited preparation is covered by the same
            # rollback as cancellation during the state commit.
            await _refresh_dialogue_pairs()
        except asyncio.CancelledError:
            # The write may already have committed when cancellation surfaced.
            # Revoke it explicitly so DB=active can never be left without a task.
            cleanup = asyncio.create_task(
                _set_state(
                    data.account_id,
                    "idle",
                    last_event="start_cancelled",
                    stopped_at=_now_iso(),
                    run_id=None,
                    expected_run_id=run_id,
                )
            )
            with suppress(asyncio.CancelledError):
                await asyncio.shield(cleanup)
            raise
        # No await after the prepared generation and task registration: a request
        # cancellation can now only land after ownership is complete.
        _spawn_runtime_task(data.account_id, run_id)
    await log_event("INFO", "warming_started", account_id=data.account_id)
    return await _current_card(data.account_id)


def account_lock(account_id: str) -> asyncio.Lock:
    """Public accessor for the per-account lifecycle lock (P2.2).

    Use this from a service-level operation that needs to hold the same lock
    ``start_warming`` / ``stop_warming`` / ``reconcile_warming_runtime`` use,
    e.g. to serialize stop + delete in ``remove_account``. The bare locked
    primitive (rather than a context manager wrapper) keeps the call site
    explicit about lock scope.
    """
    return _account_lock(account_id)


async def reconcile_warming_runtime() -> None:
    """Re-attach loop tasks for accounts whose DB state says they were running.

    ``_RUNTIME`` lives in process memory: after a restart the DB still shows
    ``active``/``sleeping``/``flood_wait`` but no task exists. We restart the
    loop for each such account so the board does not lie.

    Also refreshes the inter-account dialogue graph — ``assign_pairs`` is the
    only path that materialises pairs, so without this call the feature stays
    silently dormant. The call is idempotent (it rebuilds only when stale or
    membership-changed), so running it on every reconcile is cheap.
    """
    records = await list_warming_states()
    controls = await load_warming_settings()
    channel_count = len((await list_warming_channels()).channels)
    restarted = 0
    for record in records:
        # ``error`` is part of ``_ACTIVE_STATES`` so the UI keeps the card in
        # the warming column, but reconcile must not auto-resurrect a broken
        # account — the operator has to acknowledge and restart it.
        if not is_warming(record.state) or record.state == "error":
            continue
        # F3: take the same per-account lock as start/stop. Reconcile reads
        # state then spawns a task; without the lock, a parallel stop can
        # interleave and we end up with DB=idle + a live task.
        async with _account_lock(record.account_id):
            # Re-read inside the lock — stop_warming may have flipped this row
            # between the listing and acquiring the lock.
            fresh = await fetch_warming_state(record.account_id)
            if fresh is None or not is_warming(fresh.state) or fresh.state == "error":
                continue
            existing = _RUNTIME.get(record.account_id)
            if existing is not None and not existing.done():
                continue
            account = await fetch_account(record.account_id)
            if account is None:
                # Orphan state row — mark it stopped so the board is honest.
                await _set_state(
                    record.account_id,
                    "idle",
                    last_event="reconcile_orphan",
                    stopped_at=_now_iso(),
                )
                continue
            # Only gate the operator-startable cycling states. quarantine and
            # flood_wait are engine-managed recovery/cooldown states with their
            # own gates (a quarantined account is *expected* to read spam=limited
            # while it re-probes); applying the start_warming readiness gate to
            # them would abort an in-progress recovery and park it in error.
            if controls.enforce_readiness and fresh.state in ("active", "sleeping"):
                readiness = await _evaluate_account_readiness(
                    record.account_id,
                    account,
                    channel_count,
                )
                if not readiness.ready:
                    # Same gate as start_warming: a proxy that died / a fresh
                    # spam-limit / trust-critical drift mid-warming must not be
                    # silently resurrected on restart (start_warming would
                    # refuse it). Park it so the operator has to acknowledge.
                    await _set_state(
                        record.account_id,
                        "error",
                        last_event="reconcile_not_ready",
                        last_error="; ".join(readiness.reasons),
                        heartbeat_at=_now_iso(),
                    )
                    await log_event(
                        "WARNING",
                        "warming_reconcile_not_ready",
                        account_id=record.account_id,
                        extra={"reasons": readiness.reasons},
                    )
                    continue
            # P1.2: mint a fresh generation marker so this restarted loop owns
            # the row going forward; any pre-restart cycle that somehow lives
            # on (it shouldn't, post-restart, but be defensive) will see the
            # mismatch and bail.
            run_id = uuid.uuid4().hex
            await _set_state(record.account_id, fresh.state, run_id=run_id)
            _spawn_runtime_task(record.account_id, run_id)
            restarted += 1
    if restarted:
        await log_event(
            "INFO",
            "warming_runtime_reconciled",
            extra={"restarted": restarted},
        )
    await _refresh_dialogue_pairs()
    await purge_stale_history()
    _start_purge_task()


async def _purge_loop() -> None:  # pragma: no cover - long-running task body.
    """Rerun the retention sweep every ``purge_interval_hours`` until cancelled.

    ``purge_stale_history`` swallows its own errors, so a failing sweep never
    breaks the cadence. Cancelled cleanly on shutdown like the per-account loops.
    Also reshuffles the acquaintance graph so frozen/fail-health partners drop
    out on the purge cadence (``_refresh_dialogue_pairs`` swallows its own
    errors too).
    """
    interval = settings.warming.purge_interval_hours * 3600
    while True:
        await asyncio.sleep(interval)
        await purge_stale_history()
        await _refresh_dialogue_pairs()


def _start_purge_task() -> None:
    """Spawn the periodic retention sweep if one is not already running."""
    global _PURGE_TASK  # noqa: PLW0603 - single process-wide background task handle.
    if _PURGE_TASK is not None and not _PURGE_TASK.done():
        return
    _PURGE_TASK = asyncio.create_task(_purge_loop())


async def _refresh_dialogue_pairs() -> None:
    try:
        await assign_pairs()
    except Exception as exc:  # reconcile must not fail because dialogues did.
        logger.exception("dialogue pair refresh failed")
        await log_event(
            "WARNING",
            "warming_dialogue_pair_refresh_failed",
            extra={"error_type": type(exc).__name__},
        )


async def shutdown_warming_runtime() -> None:
    """Cancel every running loop and wait briefly for graceful exits."""
    await _stop_purge_task()
    if not _RUNTIME:
        return
    owned = list(_RUNTIME.items())
    for account_id, task in owned:
        _seams.revoke_lease(account_id)
        if not task.done():
            task.cancel()
    done, pending = await asyncio.wait(
        {task for _, task in owned},
        timeout=settings.warming.stop_cancel_timeout_seconds,
    )
    for account_id, task in owned:
        if task in done:
            _discard_runtime_task(account_id, task)
    if pending:
        await log_event("WARNING", "warming_shutdown_timeout", extra={"count": len(pending)})


async def _stop_purge_task() -> None:
    """Cancel and await the periodic retention sweep (no-op if not running)."""
    global _PURGE_TASK  # noqa: PLW0603 - single process-wide background task handle.
    task = _PURGE_TASK
    if task is None or task.done():
        _PURGE_TASK = None
        return
    task.cancel()
    done, _pending = await asyncio.wait(
        {task},
        timeout=settings.warming.stop_cancel_timeout_seconds,
    )
    if done:
        _PURGE_TASK = None


# The stop/graduation lifecycle lives in ``_graduation`` (file-size budget). It
# imports this module for the shared ``_RUNTIME`` / locks / ``_refresh_dialogue_pairs``
# seam, so the import lands at the bottom — after those are defined — to avoid a
# circular-import cycle. Re-exported so ``services.warming._runtime.<name>`` (and
# the package root) keep resolving these, and tests keep patching seams here.
from services.warming._graduation import (  # noqa: E402, F401 - re-export after globals are defined.
    _stop_warming_locked,
    handoff_to_neurocomment,
    promote_to_neurocomment,
    stop_warming,
    unmark_neurocomment,
)
