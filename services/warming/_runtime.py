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
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.db import (
    fetch_account,
    fetch_warming_state,
    get_spam_status,
    list_warming_channels,
    list_warming_states,
    load_warming_settings,
    purge_dialogue_messages_older_than,
    purge_logs_older_than,
    purge_sent_hashes_older_than,
)
from core.logging import log_event
from schemas.warming import (
    is_warming,
)
from services.dialogues import assign_pairs
from services.trust import account_trust_score
from services.warming import _seams
from services.warming._loop import run_loop_iteration
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
        )
        existing = _RUNTIME.get(data.account_id)
        if existing is None or existing.done():
            await _refresh_dialogue_pairs()
            _RUNTIME[data.account_id] = asyncio.create_task(_warming_loop(data.account_id))
    await log_event("INFO", "warming_started", account_id=data.account_id)
    return await _current_card(data.account_id)


async def stop_warming(data: StopWarmingRequest) -> WarmingAccountState:
    """Cancel an account's loop task and return it to the idle column.

    Awaits the task with a timeout so callers get back a settled state — a UI
    poll that re-reads the board will see a real ``idle`` row, not a still-
    running shadow loop. Stopping a ghost account (no row in ``accounts``) is
    a no-op for the DB — only the in-memory task is cleaned up.
    """
    async with _account_lock(data.account_id):
        task = _RUNTIME.pop(data.account_id, None)
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
                    account_id=data.account_id,
                    extra={"error_type": type(exc).__name__, "message": str(exc)},
                )
        account = await fetch_account(data.account_id)
        if account is not None:
            await _set_state(
                data.account_id,
                "idle",
                last_event="stopped",
                stopped_at=_now_iso(),
            )
    await log_event("INFO", "warming_stopped", account_id=data.account_id)
    await _refresh_dialogue_pairs()
    return await _current_card(data.account_id)


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
        _RUNTIME[record.account_id] = asyncio.create_task(_warming_loop(record.account_id))
        restarted += 1
    if restarted:
        await log_event(
            "INFO",
            "warming_runtime_reconciled",
            extra={"restarted": restarted},
        )
    await _refresh_dialogue_pairs()
    await _purge_stale_history()


async def _refresh_dialogue_pairs() -> None:
    try:
        await assign_pairs()
    except Exception as exc:  # noqa: BLE001 - reconcile must not fail because dialogues did.
        await log_event(
            "WARNING",
            "warming_dialogue_pair_refresh_failed",
            extra={"error": str(exc)},
        )


async def _purge_stale_history() -> None:
    """Best-effort retention pass on append-only tables (logs / dialogues / hashes).

    Each window comes from ``settings.warming``; setting a window to 0 disables
    the corresponding purge. Failures are logged and swallowed — retention is
    nice-to-have, never a reason to abort reconcile.
    """
    now = datetime.now(UTC)
    plans = [
        (
            settings.warming.log_retention_days,
            "log_retention_purged",
            purge_logs_older_than,
        ),
        (
            settings.warming.dialogue_message_retention_days,
            "dialogue_message_retention_purged",
            purge_dialogue_messages_older_than,
        ),
        (
            settings.warming.sent_hash_retention_days,
            "sent_hash_retention_purged",
            purge_sent_hashes_older_than,
        ),
    ]
    for window_days, event, purge in plans:
        if window_days <= 0:
            continue
        cutoff = (now - timedelta(days=window_days)).isoformat()
        try:
            removed = await purge(cutoff)
        except Exception as exc:  # noqa: BLE001 - retention failures must not block reconcile.
            await log_event(
                "WARNING",
                "retention_purge_failed",
                extra={"event": event, "error": str(exc)},
            )
            continue
        if removed:
            await log_event("INFO", event, extra={"removed": removed})


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


async def _warming_loop(account_id: str) -> None:  # pragma: no cover - long-running task
    """Run cycles forever, timing each from the persisted ``next_run_at``.

    Never raises to the caller. On (re)start it respects an existing schedule so
    an app restart does not turn parked accounts into an activity spike.
    """
    try:
        record = await fetch_warming_state(account_id)
        if record is not None and record.state == "error":
            return
        await asyncio.sleep(_initial_delay_seconds(record, datetime.now(UTC)))
        while True:
            await run_loop_iteration(account_id)
            record = await fetch_warming_state(account_id)
            if record is not None and record.state == "error":
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
        await _set_state(
            account_id,
            "error",
            last_event="loop_crashed",
            last_error=f"{type(exc).__name__}: {exc}",
            heartbeat_at=_now_iso(),
        )
