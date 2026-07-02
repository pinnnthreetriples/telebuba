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
import uuid
from contextlib import suppress
from typing import TYPE_CHECKING

from core.config import settings
from core.db import (
    fetch_account,
    fetch_warming_state,
    get_spam_status,
    list_warming_channels,
    list_warming_states,
    load_warming_settings,
    mark_promoted_to_nc,
    unmark_promoted_to_nc,
)
from core.logging import log_event
from schemas.warming import (
    is_warming,
)
from services.dialogues import assign_pairs
from services.trust import account_trust_score
from services.warming._purge import purge_stale_history
from services.warming._runner import _warming_loop
from services.warming._state import _current_card, _set_state
from services.warming.pacing import (
    _now_iso,
    _proxy_snapshot,
    evaluate_readiness,
)

if TYPE_CHECKING:
    from schemas.warming import (
        StartWarmingRequest,
        StopWarmingRequest,
        WarmingAccountState,
    )

# account_id -> running warming loop. Genuine runtime state (rare exception to
# the "no classes for stateless logic" rule): the loops must outlive a single
# UI handler call so the board can start/stop them.
_RUNTIME: dict[str, asyncio.Task[None]] = {}

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


def _account_lock(account_id: str) -> asyncio.Lock:
    lock = _ACCOUNT_LOCKS.get(account_id)
    if lock is None:
        lock = asyncio.Lock()
        _ACCOUNT_LOCKS[account_id] = lock
    return lock


async def start_warming(data: StartWarmingRequest) -> WarmingAccountState:
    """Move an account into the warming column and kick off its loop task."""
    async with _account_lock(data.account_id):
        account = await fetch_account(data.account_id)
        if account is None:
            msg = f"Unknown account: {data.account_id}"
            raise UnknownAccountError(msg)
        if (await load_warming_settings()).enforce_readiness:
            channel_count = len((await list_warming_channels()).channels)
            spam = await get_spam_status(data.account_id)
            trust_score = await account_trust_score(data.account_id)
            readiness = evaluate_readiness(
                account,
                channel_count,
                spam=spam,
                trust_score=trust_score,
            )
            if not readiness.ready:
                await log_event(
                    "WARNING",
                    "warming_start_blocked",
                    account_id=data.account_id,
                    extra={"reasons": readiness.reasons},
                )
                raise WarmingNotReadyError(readiness.reasons)
        # P1.2: stamp a fresh generation marker so an in-flight cycle from
        # the previous run can detect and refuse to write through.
        run_id = uuid.uuid4().hex
        # П7: restarting an already-warming account keeps the original stint
        # anchor so "дней в прогреве" counts from the first start; a genuine
        # start from idle/stopped (re)stamps it.
        existing = await fetch_warming_state(data.account_id)
        started_at = (
            existing.started_at
            if existing and existing.started_at and is_warming(existing.state)
            else _now_iso()
        )
        # Operator-chosen warming duration (the start modal's day slider). A
        # restart-while-warming keeps the original pick; a genuine (re)start
        # honours the new value, falling back to the configured floor when the
        # request omits it. The loop auto-completes the account once warming
        # reaches this many days.
        target_days = (
            existing.target_days
            if existing is not None and existing.target_days and is_warming(existing.state)
            else (data.target_days or settings.neurocomment.warmed_min_days)
        )
        # Persona mirrors started_at: a restart-while-warming keeps the original
        # cadence; a genuine (re)start from idle/stopped honours the new pick.
        activity_persona = (
            existing.activity_persona
            if existing is not None and is_warming(existing.state)
            else data.activity_persona
        )
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
        await _set_state(
            data.account_id,
            "active",
            last_event="queued",
            next_run_at=None,
            started_at=started_at,
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
            target_days=target_days,
            activity_persona=activity_persona,
        )
        # F2: an existing task may still be inside the inter-cycle
        # ``asyncio.sleep(_loop_sleep_seconds(...))`` from the *previous*
        # ``next_run_at``. We just cleared that schedule, so the only way to
        # honour the operator's "start now" is to cancel and replace the task.
        existing = _RUNTIME.pop(data.account_id, None)
        if existing is not None and not existing.done():
            existing.cancel()
            with suppress(TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(
                    asyncio.shield(existing),
                    timeout=settings.warming.stop_cancel_timeout_seconds,
                )
        await _refresh_dialogue_pairs()
        _RUNTIME[data.account_id] = asyncio.create_task(
            _warming_loop(data.account_id, run_id=run_id),
        )
    await log_event("INFO", "warming_started", account_id=data.account_id)
    return await _current_card(data.account_id)


async def _stop_warming_locked(account_id: str) -> None:
    """Inner stop, run with ``_account_lock(account_id)`` already held.

    Extracted so service-level operations that need to compose stop with
    other state mutations (e.g. ``remove_account``) can hold the lock across
    both steps. See P2.2.
    """
    task = _RUNTIME.pop(account_id, None)
    if task is not None and not task.done():
        task.cancel()
        try:
            await asyncio.wait_for(
                asyncio.shield(task),
                timeout=settings.warming.stop_cancel_timeout_seconds,
            )
        except (TimeoutError, asyncio.CancelledError):
            # Either we timed out or the cancel propagated correctly —
            # in both cases the task is no longer ours to await.
            pass
        except Exception as exc:  # noqa: BLE001 - log+continue; stop must not fail.
            await log_event(
                "WARNING",
                "warming_stop_task_error",
                account_id=account_id,
                extra={"error_type": type(exc).__name__, "message": str(exc)},
            )
    account = await fetch_account(account_id)
    if account is not None:
        # Round-4 P1.1: clear run_id when stopping so the row carries no live
        # generation. A stale loop's CAS write that targets the previous
        # run_id therefore cannot match (its WHERE turns the UPDATE into a
        # no-op). Belt; the CAS-rejects-idle clause in _upsert_warming_state
        # is the suspenders.
        await _set_state(
            account_id,
            "idle",
            last_event="stopped",
            stopped_at=_now_iso(),
            run_id=None,
        )


async def stop_warming(data: StopWarmingRequest) -> WarmingAccountState:
    """Cancel an account's loop task and return it to the idle column.

    Awaits the task with a timeout so callers get back a settled state — a UI
    poll that re-reads the board will see a real ``idle`` row, not a still-
    running shadow loop. Stopping a ghost account (no row in ``accounts``) is
    a no-op for the DB — only the in-memory task is cleaned up.
    """
    async with _account_lock(data.account_id):
        await _stop_warming_locked(data.account_id)
    await log_event("INFO", "warming_stopped", account_id=data.account_id)
    await _refresh_dialogue_pairs()
    return await _current_card(data.account_id)


async def promote_to_neurocomment(account_id: str) -> WarmingAccountState:
    """Graduate an account: stop its warming loop and flag it for the neurocomment pool.

    Two-step operation under one lock so we don't race a freshly-restarted loop:
    cancel any running task, then persist ``promoted_to_nc=True``. The card the
    caller re-renders shows the account in idle with the flag set, and the
    neurocomment warmed-account overview will pick it up on the next poll.
    """
    async with _account_lock(account_id):
        await _stop_warming_locked(account_id)
        await mark_promoted_to_nc(account_id)
    await log_event("INFO", "warming_promoted_to_neurocomment", account_id=account_id)
    await _refresh_dialogue_pairs()
    return await _current_card(account_id)


async def unmark_neurocomment(account_id: str) -> WarmingAccountState:
    """Reverse a graduation: clear ``promoted_to_nc`` (Group C un-promote button).

    Held under the per-account lock for symmetry with ``promote_to_neurocomment``
    so a concurrent re-promote / restart does not race the flip.
    """
    async with _account_lock(account_id):
        await unmark_promoted_to_nc(account_id)
    await log_event("INFO", "warming_unpromoted_from_neurocomment", account_id=account_id)
    return await _current_card(account_id)


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
                readiness = evaluate_readiness(
                    account,
                    channel_count,
                    spam=await get_spam_status(record.account_id),
                    trust_score=await account_trust_score(record.account_id),
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
            _RUNTIME[record.account_id] = asyncio.create_task(
                _warming_loop(record.account_id, run_id=run_id),
            )
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
    """
    interval = settings.warming.purge_interval_hours * 3600
    while True:
        await asyncio.sleep(interval)
        await purge_stale_history()


def _start_purge_task() -> None:
    """Spawn the periodic retention sweep if one is not already running."""
    global _PURGE_TASK  # noqa: PLW0603 - single process-wide background task handle.
    if _PURGE_TASK is not None and not _PURGE_TASK.done():
        return
    _PURGE_TASK = asyncio.create_task(_purge_loop())


async def _refresh_dialogue_pairs() -> None:
    try:
        await assign_pairs()
    except Exception as exc:  # noqa: BLE001 - reconcile must not fail because dialogues did.
        await log_event(
            "WARNING",
            "warming_dialogue_pair_refresh_failed",
            extra={"error": str(exc)},
        )


async def shutdown_warming_runtime() -> None:
    """Cancel every running loop and wait briefly for graceful exits."""
    await _stop_purge_task()
    if not _RUNTIME:
        return
    tasks = list(_RUNTIME.values())
    _RUNTIME.clear()
    for task in tasks:
        if not task.done():
            task.cancel()
    try:
        await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=settings.warming.stop_cancel_timeout_seconds,
        )
    except TimeoutError:
        await log_event("WARNING", "warming_shutdown_timeout", extra={"count": len(tasks)})


async def _stop_purge_task() -> None:
    """Cancel and await the periodic retention sweep (no-op if not running)."""
    global _PURGE_TASK  # noqa: PLW0603 - single process-wide background task handle.
    task = _PURGE_TASK
    _PURGE_TASK = None
    if task is None or task.done():
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
