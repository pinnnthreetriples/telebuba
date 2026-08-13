"""One warming loop step — gates, cycle, state transition, quarantine recovery.

``run_loop_iteration`` is the testable step the long-running loop in
:mod:`services.warming._runtime` wraps. Telegram/Gemini/spam-probe/randomness
are reached via :mod:`services.warming._seams`.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from core.config import settings
from core.db import fetch_warming_state
from core.logging import log_event
from schemas.warming import WarmingCycleRequest, WarmingCycleResult
from services.warming import _seams
from services.warming._cycle import run_one_cycle
from services.warming._fleet import _is_quiet_day
from services.warming._reservation import _reconcile_reservation, _Reservation
from services.warming._state import _set_state
from services.warming._transitions import (
    _calculate_next_run,
    _matches_active_run,
    _resolve_phase_after_cycle,
)
from services.warming.pacing import (
    _account_tz,
    _next_utc_midnight,
    _now_iso,
    _shift_to_active_hours,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime

    from schemas.warming import ActivityPersona, WarmingState
    from services.warming._steps import _ChannelTally

    _Schedule = tuple[int, datetime, WarmingState]

# A cycle always spends one action on the SetOnline presence flip; require room
# for at least one real action (join/read/react) beyond it before starting, or
# the cycle would burn a 12-30h sleep doing nothing useful.
_MIN_CYCLE_ACTIONS = 2

# Canonical order of the live-progress tokens ``run_one_cycle`` emits. The loop
# maps a token to its position here to keep the rail advancing forward only —
# the channel loop revisits join/read/react per channel, so a raw write would
# bounce the rail backward.
_PROGRESS_STEPS: tuple[str, ...] = ("set_online", "join", "read", "react", "stories", "send_dm")

# Fleet-wide ceiling on concurrently-running Telegram-heavy cycles (see
# ``warming.cycle_concurrency``). Module-level and loop-bound like the runtime's
# lock dicts, so a restart's reconcile — which recreates one loop task per active
# account — can't drive dozens of simultaneous cycles. Acquired only around
# ``run_one_cycle`` below, never the long inter-cycle sleep. Tests reset it (the
# conftest rebinds it per test, mirroring ``_ACCOUNT_LOCKS``).
_cycle_semaphore = asyncio.Semaphore(settings.warming.cycle_concurrency)


async def _gate_quiet_day(
    account_id: str,
    daily: tuple[int, str],
    now: datetime,
    *,
    run_id: str | None,
) -> WarmingCycleResult | None:
    """Park until tomorrow if today is this account's quiet day.

    A quiet day mimics the natural gaps in a real user's activity — some days
    they just don't open the app. Parks in ``sleeping`` until the next UTC
    midnight (shifted into the active window), exactly like the daily-cap gate,
    so the loop resumes and re-evaluates for the fresh calendar day. Because the
    decision is deterministic per ``(account, date)`` and parks past midnight, it
    fires at most once per calendar day.
    """
    daily_count, daily_date = daily
    if not _is_quiet_day(account_id, daily_date):
        return None
    next_run = _shift_to_active_hours(
        _next_utc_midnight(now),
        await _account_tz(account_id),
        _seams.rng,
        account_id,
    ).isoformat()
    write = await _set_state(
        account_id,
        "sleeping",
        last_event="quiet_day",
        next_run_at=next_run,
        heartbeat_at=now.isoformat(),
        daily_actions=daily_count,
        daily_count_date=daily_date,
        expected_run_id=run_id,
    )
    if run_id is not None and not write.applied:
        return WarmingCycleResult(account_id=account_id, status="skipped", detail="stale run")
    return WarmingCycleResult(account_id=account_id, status="skipped", detail="quiet day")


async def _gate_daily_limit(
    account_id: str,
    effective_cap: int,
    daily: tuple[int, str],
    now: datetime,
    *,
    run_id: str | None,
) -> WarmingCycleResult | None:
    """Park if the per-account daily action cap has been reached.

    ``daily`` is the ``(count, iso_date)`` pair from :func:`_roll_daily`.
    Returns the terminal ``WarmingCycleResult`` when the iteration should
    exit early, or ``None`` when the cycle may proceed.
    """
    daily_count, daily_date = daily
    # Leave room for at least one real action after the mandatory SetOnline:
    # a cycle that could only fit the presence ping would burn a 12-30h sleep on
    # zero warming work, so park instead of starting it (#100). Floor the headroom
    # at the cap itself so a tiny cap (e.g. a legacy .env override of 1) still runs
    # once a day rather than being parked forever.
    headroom = min(_MIN_CYCLE_ACTIONS, effective_cap)
    if effective_cap <= 0 or daily_count <= effective_cap - headroom:
        return None
    next_run = _shift_to_active_hours(
        _next_utc_midnight(now),
        await _account_tz(account_id),
        _seams.rng,
        account_id,
    ).isoformat()
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


async def _finalize_after_cycle(  # noqa: PLR0913 - explicit post-cycle inputs read clearer than a bag.
    account_id: str,
    result: WarmingCycleResult,
    age_hours: float,
    reservation: _Reservation,
    schedule: _Schedule,
    *,
    run_id: str | None,
) -> WarmingCycleResult:
    """Write the post-cycle state, honouring concurrent stop/restart.

    ``schedule`` is the ``(actions_done, next_run_dt, next_state)`` triple the
    caller computed via :func:`_calculate_next_run` (kept out of here so the
    parameter list stays small). F1 + P1.2: if ``stop_warming`` wrote ``idle``
    OR ``start_warming`` minted a fresh ``run_id`` while we were inside
    ``run_one_cycle``, do not resurrect the cycle's ``next_state`` on top of it.
    The CAS clause on the final write provides the same guarantee even when the
    run_id flips between this read and the write (Round-2 P1 + Round-4 P1.1).
    """
    daily_count, daily_date = reservation.daily_count, reservation.daily_date
    actions_done, next_run_dt, next_state = schedule
    new_daily = daily_count + actions_done
    next_run = next_run_dt.isoformat()

    latest = await fetch_warming_state(account_id)
    if not _matches_active_run(latest, run_id):
        # The row moved on (a stop wrote ``idle``, a restart minted a new run_id,
        # or a readiness park wrote ``error``) so the transition below must not
        # land — but the reservation is still ours to release, and only its own
        # booked value decides that, not the generation that now owns the row.
        if latest is not None:
            await _reconcile_reservation(account_id, reservation, actions_done)
        return result
    if latest is not None and latest.state == "idle":
        return result

    new_phase, phase_entered_iso, phase_event = await _resolve_phase_after_cycle(
        account_id,
        age_hours,
        latest,
    )
    write = await _set_state(
        account_id,
        next_state,
        last_event=f"cycle:{result.status}",
        last_cycle_at=_now_iso(),
        next_run_at=next_run,
        # П9: a "skipped" cycle (no channels configured) did no warming work,
        # so it must not bump the counter and fake progress. Every other status
        # (ok/failed/flood/peer_flood) ran real actions and counts.
        increment_cycle=result.status != "skipped",
        heartbeat_at=_now_iso(),
        last_action=result.last_failed_action,
        last_channel=result.last_failed_channel,
        last_error=result.detail,
        flood_wait_seconds=result.flood_wait_seconds,
        flood_wait_until=result.flood_wait_until,
        daily_actions=new_daily,
        daily_count_date=daily_date,
        expected_run_id=run_id,
        current_phase=new_phase,
        phase_entered_at=phase_entered_iso,
    )
    # Announce the phase transition only once the write actually landed: if the
    # CAS rejected it (a newer generation took the row between the read above and
    # this write), the transition never happened, so a logged event would be a
    # phantom (#100).
    if phase_event is not None and (run_id is None or write.applied):
        await log_event(
            phase_event.level,
            "warming_phase_advanced",
            account_id=account_id,
            extra=phase_event.extra,
        )
    return result


async def _execute_cycle(
    request: WarmingCycleRequest,
    *,
    on_step: Callable[[str], Awaitable[None]],
    tally: _ChannelTally,
) -> WarmingCycleResult:
    """Patch-friendly indirection retained for tests and engine instrumentation."""
    return await run_one_cycle(request, on_step=on_step, tally=tally)


async def _schedule_next_run(
    account_id: str,
    result: WarmingCycleResult,
    persona: ActivityPersona,
    effective_cap: int,
) -> _Schedule:
    """Patch-friendly scheduling indirection used by the iteration slice."""
    return await _calculate_next_run(account_id, result, persona, effective_cap)


from services.warming._iteration import run_loop_iteration  # noqa: E402, F401
