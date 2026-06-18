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
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.config import settings
from core.db import (
    fetch_account,
    fetch_warming_state,
    get_spam_status,
    list_warming_channels,
    list_warming_states,
    load_warming_settings,
)
from core.logging import log_event
from schemas.warming import (
    is_warming,
)
from services.dialogues import assign_pairs
from services.trust import account_trust_score
from services.warming import _seams
from services.warming._loop import run_loop_iteration
from services.warming._purge import purge_stale_history
from services.warming._state import _current_card, _set_state
from services.warming.pacing import (
    _SECONDS_PER_HOUR,
    _now_iso,
    _proxy_snapshot,
    _seconds_until,
    evaluate_readiness,
)

if TYPE_CHECKING:
    from schemas.warming import (
        StartWarmingRequest,
        StopWarmingRequest,
        WarmingAccountState,
        WarmingStateRecord,
    )

# account_id -> running warming loop. Genuine runtime state (rare exception to
# the "no classes for stateless logic" rule): the loops must outlive a single
# UI handler call so the board can start/stop them.
_RUNTIME: dict[str, asyncio.Task[None]] = {}

# Per-account async lock: prevents concurrent start/stop interleaving from
# leaving the DB and ``_RUNTIME`` in mismatched states. Locks are created lazily
# and never freed — the dictionary is bounded by the number of accounts.
_ACCOUNT_LOCKS: dict[str, asyncio.Lock] = {}


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


def _is_live_generation(record: WarmingStateRecord | None, run_id: str | None) -> bool:
    """True iff ``record`` belongs to ``run_id`` and is still in a warming state.

    P1.2: ``run_id is None`` means the loop wasn't given a generation marker
    (legacy reconcile from a DB that pre-dates migration #8); we fall back to
    state-only checks so behaviour matches the pre-P1.2 baseline.
    """
    if record is None:
        return False
    if not is_warming(record.state) or record.state == "error":
        return False
    if run_id is None:
        return True
    return record.run_id == run_id


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
        await _set_state(
            data.account_id,
            "active",
            last_event="queued",
            next_run_at=None,
            started_at=_now_iso(),
            stopped_at=None,
            last_error=None,
            flood_wait_seconds=None,
            flood_wait_until=None,
            proxy_snapshot=_proxy_snapshot(account),
            run_id=run_id,
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


def _loop_sleep_seconds(record: WarmingStateRecord | None, now: datetime) -> float:
    """Seconds to wait before the next cycle, from the persisted ``next_run_at``.

    Falls back to a fresh randomised 12-30h sleep only if the schedule is missing
    (it never should be after ``run_loop_iteration`` writes one).
    """
    if record is not None and record.next_run_at is not None:
        return _seconds_until(record.next_run_at, now)
    warm = settings.warming
    return _seams.rng.uniform(
        warm.cycle_sleep_min_hours * _SECONDS_PER_HOUR,
        warm.cycle_sleep_max_hours * _SECONDS_PER_HOUR,
    )


def _initial_delay_seconds(record: WarmingStateRecord | None, now: datetime) -> float:
    """Delay before the first cycle after (re)starting a loop.

    Honours a persisted future ``next_run_at`` so a restart resumes the existing
    schedule; a fresh account (no schedule yet) only waits a short startup jitter.
    """
    if record is not None and record.next_run_at is not None:
        return _seconds_until(record.next_run_at, now)
    return _seams.rng.uniform(0.0, settings.warming.startup_jitter_max_seconds)


async def _warming_loop(
    account_id: str,
    *,
    run_id: str | None = None,
) -> None:  # pragma: no cover - long-running task
    """Run cycles forever, timing each from the persisted ``next_run_at``.

    Never raises to the caller. On (re)start it respects an existing schedule so
    an app restart does not turn parked accounts into an activity spike.

    ``run_id`` is the generation marker the caller stamped before creating this
    task. The loop refuses to keep running if the DB ``run_id`` no longer
    matches (= a newer ``start_warming`` minted a fresh generation), and passes
    it to ``run_loop_iteration`` so an in-flight cycle won't write through after
    a restart either (P1.2).

    Round-6 P1: the crash handler also runs the generation check + CAS. Without
    it, a stale loop that crashed after a restart would stamp ``error`` over
    the new generation's row, undoing the restart.
    """
    try:
        record = await fetch_warming_state(account_id)
        if not _is_live_generation(record, run_id):
            return
        await asyncio.sleep(_initial_delay_seconds(record, datetime.now(UTC)))
        while True:
            record = await fetch_warming_state(account_id)
            if not _is_live_generation(record, run_id):
                break
            await run_loop_iteration(account_id, run_id=run_id)
            record = await fetch_warming_state(account_id)
            if not _is_live_generation(record, run_id):
                break
            await asyncio.sleep(_loop_sleep_seconds(record, datetime.now(UTC)))
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - a background loop must never crash silently.
        await log_event(
            "ERROR",
            "warming_loop_crashed",
            account_id=account_id,
            extra={"error_type": type(exc).__name__, "message": str(exc)},
        )
        # Round-6 P1: pre-check generation so a stale crash does not even try
        # to write. The CAS predicate below is the suspenders — if our pre-read
        # raced a restart, the upsert still refuses to overwrite a fresh
        # generation's row.
        latest = await fetch_warming_state(account_id)
        if not _is_live_generation(latest, run_id):
            return
        await _set_state(
            account_id,
            "error",
            last_event="loop_crashed",
            last_error=f"{type(exc).__name__}: {exc}",
            heartbeat_at=_now_iso(),
            expected_run_id=run_id,
        )
