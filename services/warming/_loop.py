"""One warming loop step — gates, cycle, state transition, quarantine recovery.

``run_loop_iteration`` is the testable step the long-running loop in
:mod:`services.warming._runtime` wraps. Telegram/Gemini/spam-probe/randomness
are reached via :mod:`services.warming._seams`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.db import fetch_warming_state, load_warming_settings
from core.logging import log_event
from schemas.warming import WarmingCycleRequest, WarmingCycleResult
from services.warming import _seams
from services.warming._cycle import run_one_cycle
from services.warming._state import _set_state
from services.warming.pacing import (
    _SECONDS_PER_HOUR,
    _account_tz,
    _in_quiet_hours,
    _local_now,
    _next_utc_midnight,
    _now_iso,
    _quiet_hours_end_at,
    _roll_daily,
    _shift_to_active_hours,
)

if TYPE_CHECKING:
    from schemas.warming import WarmingState, WarmingStateRecord


def _matches_active_run(record: WarmingStateRecord | None, run_id: str | None) -> bool:
    """True iff ``record`` is alive and (when supplied) matches ``run_id`` (P1.2).

    ``error`` and ``idle`` are both terminal as far as the runtime loop is
    concerned (the operator has to ack and restart an error'd account; idle
    is the stopped state). Without rejecting ``error`` here, a direct
    ``run_loop_iteration(account_id)`` call (run_id=None) could resurrect a
    cycle on an account the runtime wrapper would have refused to start —
    inconsistent with reconcile, which skips error rows.
    """
    if record is None:
        return run_id is None  # no DB row + no expectation → trivially "match"
    if record.state in ("idle", "error"):
        return False
    if run_id is None:
        return True
    return record.run_id == run_id


async def _recover_from_quarantine(
    account_id: str,
    record: WarmingStateRecord,
    now: datetime,
    *,
    run_id: str | None = None,
) -> WarmingCycleResult:
    """Re-check a quarantined account: resume if cleared, escalate otherwise.

    Called when a quarantine window has elapsed. Re-probes @SpamBot; a cleared
    account returns to warming, a still-limited one is re-quarantined until the
    configured repeat cap, after which it is given up on (error + alert).

    ``run_id`` (Round-2 P1 + Round-5 P1): if supplied, every write is
    CAS-guarded against the row's current run_id. A new CAS-write fires
    *before* ``refresh_spam_status`` so a stale loop does not issue the
    external @SpamBot probe on behalf of a generation that's already been
    replaced — the round-4 P1.2 fix only protected the regular cycle path,
    quarantine was still open.
    """
    warm = settings.warming
    # Round-5 P1: pre-probe CAS. Telegram I/O lives behind this gate.
    probe_started = await _set_state(
        account_id,
        "quarantine",
        last_event="quarantine_probe_started",
        heartbeat_at=now.isoformat(),
        quarantine_count=record.quarantine_count,
        expected_run_id=run_id,
    )
    if run_id is not None and not probe_started.applied:
        return WarmingCycleResult(account_id=account_id, status="skipped", detail="stale run")

    verdict = await _seams.refresh_spam_status(account_id, force=True)
    if verdict.status != "limited":
        next_run = (now + timedelta(seconds=warm.startup_jitter_max_seconds)).isoformat()
        await _set_state(
            account_id,
            "sleeping",
            last_event="quarantine_recovered",
            next_run_at=next_run,
            heartbeat_at=now.isoformat(),
            last_error=None,
            quarantine_count=0,
            expected_run_id=run_id,
        )
        await log_event("INFO", "warming_quarantine_recovered", account_id=account_id)
        return WarmingCycleResult(account_id=account_id, status="skipped", detail="recovered")

    count = record.quarantine_count + 1
    if count >= warm.quarantine_max_repeats:
        await _set_state(
            account_id,
            "error",
            last_event="quarantine_exhausted",
            last_error=f"peer-flood not lifted after {count} checks",
            heartbeat_at=now.isoformat(),
            quarantine_count=count,
            expected_run_id=run_id,
        )
        await log_event(
            "ERROR",
            "warming_quarantine_exhausted",
            account_id=account_id,
            extra={"checks": count},
        )
        return WarmingCycleResult(
            account_id=account_id,
            status="error",
            detail="quarantine exhausted",
        )

    next_run = (now + timedelta(hours=warm.quarantine_hours)).isoformat()
    await _set_state(
        account_id,
        "quarantine",
        last_event="quarantine_extended",
        next_run_at=next_run,
        heartbeat_at=now.isoformat(),
        quarantine_count=count,
        expected_run_id=run_id,
    )
    await log_event(
        "WARNING",
        "warming_quarantine_extended",
        account_id=account_id,
        extra={"checks": count},
    )
    return WarmingCycleResult(account_id=account_id, status="skipped", detail="quarantine extended")


async def _calculate_next_run(
    account_id: str,
    result: WarmingCycleResult,
) -> tuple[int, datetime, WarmingState]:
    warm = settings.warming
    actions_done = result.attempted_actions

    next_state: WarmingState
    if result.status == "peer_flood":
        sleep_seconds = warm.quarantine_hours * _SECONDS_PER_HOUR
        next_state = "quarantine"
    elif result.status == "flood_wait":
        sleep_seconds = float(result.flood_wait_seconds) if result.flood_wait_seconds else 0.0
        next_state = "flood_wait"
    elif result.status == "failed":
        sleep_seconds = _seams.rng.uniform(
            warm.cycle_sleep_min_hours * _SECONDS_PER_HOUR,
            warm.cycle_sleep_max_hours * _SECONDS_PER_HOUR,
        )
        work_actions = (
            result.channels_joined
            + result.channels_read
            + result.reactions_sent
            + result.messages_sent
        )
        next_state = "sleeping" if work_actions > 0 else "error"
    else:
        sleep_seconds = _seams.rng.uniform(
            warm.cycle_sleep_min_hours * _SECONDS_PER_HOUR,
            warm.cycle_sleep_max_hours * _SECONDS_PER_HOUR,
        )
        next_state = "sleeping"

    next_run_dt = datetime.now(UTC) + timedelta(seconds=sleep_seconds)
    if result.status not in {"peer_flood", "flood_wait"}:
        next_run_dt = _shift_to_active_hours(next_run_dt, await _account_tz(account_id))

    return actions_done, next_run_dt, next_state


async def run_loop_iteration(  # noqa: C901, PLR0911 - explicit early-exit branches read clearer than chained conditions.
    account_id: str,
    *,
    run_id: str | None = None,
) -> WarmingCycleResult:
    """Run one iteration of the warming loop (cycle + state transitions).

    Extracted from ``_warming_loop`` so it can be tested without the infinite
    ``while True`` wrapper. Updates DB state but does NOT sleep — it writes
    ``next_run_at``, the single source of truth the loop reads to time the next
    cycle (so a restart resumes the existing schedule instead of firing early).

    Two gates run before the cycle: quiet hours (park until the window ends) and
    the per-account daily action budget (park until UTC midnight).

    ``run_id`` (P1.2 + Round-2 P1 + Round-4 P1.2): when the wrapper passes a
    non-None generation marker, every state write is CAS-guarded against it
    and the upsert reports ``applied`` so we can detect a CAS no-op caused by
    a concurrent stop/restart. If the ``cycle_started`` CAS no-ops we bail
    *before* calling ``run_one_cycle`` — otherwise the stale loop would have
    issued real Telegram actions on behalf of a generation that's already
    been replaced.
    """
    now = datetime.now(UTC)
    controls = await load_warming_settings()
    record = await fetch_warming_state(account_id)

    # P1.1 / P1.2: bail before any write if the iteration is stale.
    if not _matches_active_run(record, run_id):
        return WarmingCycleResult(account_id=account_id, status="skipped", detail="stale run")

    if record is not None and record.state == "quarantine":
        return await _recover_from_quarantine(account_id, record, now, run_id=run_id)

    if controls.quiet_hours_enabled:
        local_now = await _local_now(account_id, now)
        if _in_quiet_hours(local_now, controls.quiet_hours_start, controls.quiet_hours_end):
            next_run = _quiet_hours_end_at(local_now, controls.quiet_hours_end).isoformat()
            write = await _set_state(
                account_id,
                "sleeping",
                last_event="quiet_hours",
                next_run_at=next_run,
                heartbeat_at=now.isoformat(),
                expected_run_id=run_id,
            )
            if run_id is not None and not write.applied:
                return WarmingCycleResult(
                    account_id=account_id, status="skipped", detail="stale run"
                )
            return WarmingCycleResult(account_id=account_id, status="skipped", detail="quiet hours")

    daily_count, daily_date = _roll_daily(record, now.date().isoformat())
    if controls.max_daily_actions > 0 and daily_count >= controls.max_daily_actions:
        next_run = _next_utc_midnight(now).isoformat()
        write = await _set_state(
            account_id,
            "sleeping",
            last_event="daily_limit",
            next_run_at=next_run,
            heartbeat_at=now.isoformat(),
            daily_actions=daily_count,
            daily_count_date=daily_date,
            expected_run_id=run_id,
        )
        if run_id is not None and not write.applied:
            return WarmingCycleResult(account_id=account_id, status="skipped", detail="stale run")
        return WarmingCycleResult(account_id=account_id, status="skipped", detail="daily limit")

    # Round-4 P1.2: if cycle_started CAS-no-ops, abort BEFORE the cycle. The
    # real cost of the bug is the Telegram I/O we'd otherwise issue here on
    # behalf of a stale generation.
    started = await _set_state(
        account_id,
        "active",
        last_event="cycle_started",
        heartbeat_at=now.isoformat(),
        last_error=None,
        daily_actions=daily_count,
        daily_count_date=daily_date,
        expected_run_id=run_id,
    )
    if run_id is not None and not started.applied:
        return WarmingCycleResult(account_id=account_id, status="skipped", detail="stale run")

    remaining = None
    if controls.max_daily_actions > 0:
        remaining = max(0, controls.max_daily_actions - daily_count)

    result = await run_one_cycle(
        WarmingCycleRequest(
            account_id=account_id,
            remaining_actions=remaining,
        )
    )
    actions_done, next_run_dt, next_state = await _calculate_next_run(account_id, result)
    new_daily = daily_count + actions_done
    next_run = next_run_dt.isoformat()

    # F1 + P1.2: if stop_warming wrote ``idle`` OR start_warming minted a fresh
    # run_id while we were inside run_one_cycle, do not resurrect the cycle's
    # next_state on top of it. The cycle's I/O may have outlived task.cancel()
    # (e.g. asyncio.to_thread isn't interrupted mid-write), and the operator's
    # intent must win. The CAS clause on the final _set_state below provides
    # the same guarantee even when the run_id flips between this read and the
    # next write (Round-2 P1 + Round-4 P1.1).
    latest = await fetch_warming_state(account_id)
    if not _matches_active_run(latest, run_id):
        return result
    if latest is not None and latest.state == "idle":
        return result

    await _set_state(
        account_id,
        next_state,
        last_event=f"cycle:{result.status}",
        last_cycle_at=_now_iso(),
        next_run_at=next_run,
        increment_cycle=True,
        heartbeat_at=_now_iso(),
        last_action=result.last_failed_action,
        last_channel=result.last_failed_channel,
        last_error=result.detail,
        flood_wait_seconds=result.flood_wait_seconds,
        flood_wait_until=result.flood_wait_until,
        daily_actions=new_daily,
        daily_count_date=daily_date,
        expected_run_id=run_id,
    )
    return result
