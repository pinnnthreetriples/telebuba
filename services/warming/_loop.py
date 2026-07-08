"""One warming loop step — gates, cycle, state transition, quarantine recovery.

``run_loop_iteration`` is the testable step the long-running loop in
:mod:`services.warming._runtime` wraps. Telegram/Gemini/spam-probe/randomness
are reached via :mod:`services.warming._seams`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.db import fetch_account, fetch_warming_state, load_warming_settings
from core.logging import log_event
from schemas.warming import WarmingCycleRequest, WarmingCycleResult
from services.trust import account_trust_score
from services.warming import _seams
from services.warming._cycle import run_one_cycle
from services.warming._state import _set_state
from services.warming._transitions import (
    _calculate_next_run,
    _gate_readiness,
    _gate_target_reached,
    _matches_active_run,
    _resolve_phase_after_cycle,
)
from services.warming.pacing import (
    _account_age_hours,
    _account_tz,
    _next_utc_midnight,
    _now_iso,
    _roll_daily,
    _shift_to_active_hours,
    compute_intensity,
)

if TYPE_CHECKING:
    from schemas.warming import WarmingState, WarmingStateRecord

    _Schedule = tuple[int, datetime, WarmingState]


# A cycle always spends one action on the SetOnline presence flip; require room
# for at least one real action (join/read/react) beyond it before starting, or
# the cycle would burn a 12-30h sleep doing nothing useful.
_MIN_CYCLE_ACTIONS = 2

# Canonical order of the live-progress tokens ``run_one_cycle`` emits. The loop
# maps a token to its position here to keep the rail advancing forward only —
# the channel loop revisits join/read/react per channel, so a raw write would
# bounce the rail backward.
_PROGRESS_STEPS: tuple[str, ...] = ("set_online", "join", "read", "react", "send_dm")


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
    daily: tuple[int, str],
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
    daily_count, daily_date = daily
    actions_done, next_run_dt, next_state = schedule
    new_daily = daily_count + actions_done
    next_run = next_run_dt.isoformat()

    latest = await fetch_warming_state(account_id)
    if not _matches_active_run(latest, run_id):
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
            "phase_advanced",
            account_id=account_id,
            extra=phase_event.extra,
        )
    return result


async def run_loop_iteration(  # noqa: PLR0911 - sequential pre-cycle gates, each early-exits.
    account_id: str,
    *,
    run_id: str | None = None,
) -> WarmingCycleResult:
    """Run one iteration of the warming loop (cycle + state transitions).

    Updates DB state but does NOT sleep — writes ``next_run_at`` instead,
    so a restart resumes the existing schedule. When ``run_id`` is set,
    every state write is CAS-guarded against it so a concurrent
    stop/restart wins over a stale cycle.
    """
    now = datetime.now(UTC)
    controls = await load_warming_settings()
    record = await fetch_warming_state(account_id)

    if not _matches_active_run(record, run_id):
        return WarmingCycleResult(account_id=account_id, status="skipped", detail="stale run")

    done = await _gate_target_reached(account_id, record, now, run_id=run_id)
    if done is not None:
        return done

    if record is not None and record.state == "quarantine":
        return await _recover_from_quarantine(account_id, record, now, run_id=run_id)

    account = await fetch_account(account_id)
    age_hours = _account_age_hours(account, now)
    trust = await account_trust_score(account_id)

    not_ready = await _gate_readiness(account, controls, record, trust, now, run_id=run_id)
    if not_ready is not None:
        return not_ready

    intensity = compute_intensity(age_hours, trust_band=trust.band)
    # П2: the per-account auto cap (phase + trust band) is the single source of
    # truth. The legacy fleet-wide ``controls.max_daily_actions`` override is
    # retired — no longer read here, so a stale nonzero DB value can no longer
    # silently neuter the auto cap (and there was no UI to clear it).
    effective_cap = intensity.daily_cap

    daily = _roll_daily(record, now.date().isoformat())
    daily_count, daily_date = daily
    gated = await _gate_daily_limit(account_id, effective_cap, daily, now, run_id=run_id)
    if gated is not None:
        return gated

    started = await _set_state(
        account_id,
        "active",
        last_event="cycle_started",
        heartbeat_at=now.isoformat(),
        last_error=None,
        last_action="set_online",
        last_channel=None,
        daily_actions=daily_count,
        daily_count_date=daily_date,
        expected_run_id=run_id,
    )
    if run_id is not None and not started.applied:
        return WarmingCycleResult(account_id=account_id, status="skipped", detail="stale run")

    # set_online is already seeded on the cycle_started write above, so the rail
    # lights up "online" the moment the cycle starts; the hook advances from join.
    max_step = 0

    async def _on_step(step: str) -> None:
        # ponytail: best-effort, monotonic progress write. CAS-guarded by run_id
        # but the result is ignored — the cycle isn't abortable mid-flight, so a
        # stale generation's write degrades to a no-op. A raising write (e.g. a
        # transient SQLite lock) is swallowed too: this is cosmetic rail progress
        # and must never abort the live cycle or park a healthy account in error.
        nonlocal max_step
        idx = _PROGRESS_STEPS.index(step) if step in _PROGRESS_STEPS else -1
        if idx <= max_step:
            return
        max_step = idx
        try:
            await _set_state(
                account_id,
                "active",
                last_action=step,
                heartbeat_at=_now_iso(),
                expected_run_id=run_id,
            )
        except Exception as exc:  # noqa: BLE001 - cosmetic progress, never abort the cycle.
            await log_event(
                "WARNING",
                "warming_progress_write_failed",
                account_id=account_id,
                extra={"step": step, "error_type": type(exc).__name__, "message": str(exc)},
            )

    persona = record.activity_persona if record is not None else "normal"
    remaining = max(0, effective_cap - daily_count) if effective_cap > 0 else None
    result = await run_one_cycle(
        WarmingCycleRequest(
            account_id=account_id,
            remaining_actions=remaining,
            # П11: trust+age-aware DM permission (readiness already enforced by
            # the gate above when enabled). The cycle's own intensity is
            # trust-blind, so pass the loop's trust-aware value instead.
            dm_allowed=intensity.dm_allowed,
            activity_persona=persona,
        ),
        on_step=_on_step,
    )
    schedule = await _calculate_next_run(account_id, result, persona, effective_cap)
    return await _finalize_after_cycle(
        account_id, result, age_hours, daily, schedule, run_id=run_id
    )
