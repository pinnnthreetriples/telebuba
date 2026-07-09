"""Post-cycle state-transition helpers for the warming loop.

Computations split out of :mod:`services.warming._loop` to keep that module under
the file-size cap: which run is still live, the next-run schedule + state for a
finished cycle, and the lifecycle-phase transition (returned as a pending event
so the loop only logs it once the CAS write actually lands).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, NamedTuple

from core.config import settings
from core.db import get_spam_status, list_warming_channels
from core.logging import log_event
from schemas.warming import WarmingCycleResult
from services.trust import account_trust_score
from services.warming import _seams
from services.warming._state import _set_state
from services.warming.pacing import (
    _PHASE_ORDER,
    _SECONDS_PER_HOUR,
    _account_tz,
    _next_utc_midnight,
    _now_iso,
    _shift_to_active_hours,
    compute_intensity,
    evaluate_readiness,
    persona_next_run_seconds,
    warming_days_since,
)

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.logs import LogLevel
    from schemas.trust import TrustScore
    from schemas.warming import (
        ActivityPersona,
        WarmingPhase,
        WarmingSettingsSecret,
        WarmingState,
        WarmingStateRecord,
    )


class _PhaseEvent(NamedTuple):
    """A pending ``phase_advanced`` log entry, emitted only if the write lands."""

    level: LogLevel
    extra: dict[str, object]


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


async def _gate_readiness(  # noqa: PLR0913 - explicit readiness signals read clearer than a bag.
    account: AccountRead | None,
    controls: WarmingSettingsSecret,
    record: WarmingStateRecord | None,
    trust: TrustScore,
    now: datetime,
    *,
    run_id: str | None,
) -> WarmingCycleResult | None:
    """Park the account if readiness degraded mid-warming (audit П3).

    Re-gates the operator-cycling states (active/sleeping) before each cycle so a
    degraded account — dead proxy, spam-limited, trust-critical, channels removed
    — is parked to error rather than warmed on while the card already shows the
    blocker. quarantine returns earlier and flood_wait is an engine-managed
    cooldown, so both are skipped (mirrors ``reconcile_warming_runtime``). Returns
    the terminal ``WarmingCycleResult`` when the iteration should exit, else
    ``None`` when the cycle may proceed.
    """
    if (
        not controls.enforce_readiness
        or account is None
        or record is None
        or record.state not in ("active", "sleeping")
    ):
        return None
    account_id = account.account_id
    channel_count = len((await list_warming_channels()).channels)
    readiness = evaluate_readiness(
        account,
        channel_count,
        spam=await get_spam_status(account_id),
        trust_score=trust,
    )
    if readiness.ready:
        return None
    reasons = "; ".join(readiness.reasons)
    write = await _set_state(
        account_id,
        "error",
        last_event="cycle_not_ready",
        last_error=reasons,
        heartbeat_at=now.isoformat(),
        expected_run_id=run_id,
    )
    # A concurrent stop/restart flipped the run_id; if the CAS rejected our park,
    # a newer generation owns the row, so don't log a phantom (mirrors the gates).
    if run_id is not None and not write.applied:
        return WarmingCycleResult(account_id=account_id, status="skipped", detail="stale run")
    await log_event(
        "WARNING",
        "warming_cycle_not_ready",
        account_id=account_id,
        extra={"reasons": readiness.reasons},
    )
    return WarmingCycleResult(account_id=account_id, status="error", detail=reasons)


async def _gate_target_reached(
    account_id: str,
    record: WarmingStateRecord | None,
    now: datetime,
    *,
    run_id: str | None,
) -> WarmingCycleResult | None:
    """Stop warming once the operator-chosen ``target_days`` has elapsed.

    The account is parked in ``sleeping`` (still a warming state, so it stays in
    the warming column) with ``last_event="warming_complete"`` — the card derives
    its completion UI + hand-off button from ``warming_days >= target_days``, and
    the loop stops doing warming work. Manual ``stop_warming`` is unaffected.
    Idempotent: a row already flagged complete re-parks silently (no re-log).

    Completion is only declared from a healthy cycling state: an account under an
    active restriction (``quarantine`` peer-flood / ``flood_wait`` cooldown) that
    happens to cross ``target_days`` is skipped here so its recovery probe still
    runs — otherwise a still-restricted account would be presented as complete and
    could be graduated into the neurocomment pool.
    """
    if record is None or record.target_days is None or record.state in ("quarantine", "flood_wait"):
        return None
    days = warming_days_since(record.started_at, now)
    if days is None or days < record.target_days:
        return None
    if record.last_event == "warming_complete":
        # Idempotent re-park: rewrite a fresh future ``next_run_at`` so the loop
        # sleeps a positive interval instead of busy-spinning once the previously
        # parked midnight has passed (``_seconds_until`` clamps a past time to 0).
        # No re-log — completion was already announced on the first pass.
        write = await _set_state(
            account_id,
            "sleeping",
            last_event="warming_complete",
            next_run_at=_next_utc_midnight(now).isoformat(),
            heartbeat_at=now.isoformat(),
            expected_run_id=run_id,
        )
        if run_id is not None and not write.applied:
            return WarmingCycleResult(account_id=account_id, status="skipped", detail="stale run")
        return WarmingCycleResult(account_id=account_id, status="skipped", detail="target reached")
    write = await _set_state(
        account_id,
        "sleeping",
        last_event="warming_complete",
        next_run_at=_next_utc_midnight(now).isoformat(),
        heartbeat_at=now.isoformat(),
        expected_run_id=run_id,
    )
    if run_id is not None and not write.applied:
        return WarmingCycleResult(account_id=account_id, status="skipped", detail="stale run")
    await log_event(
        "INFO",
        "warming_target_reached",
        account_id=account_id,
        extra={"target_days": record.target_days, "warming_days": days},
    )
    return WarmingCycleResult(account_id=account_id, status="skipped", detail="target reached")


async def _calculate_next_run(
    account_id: str,
    result: WarmingCycleResult,
    persona: ActivityPersona,
    daily_cap: int,
) -> tuple[int, datetime, WarmingState]:
    warm = settings.warming
    actions_done = result.attempted_actions

    next_state: WarmingState
    if result.status == "peer_flood":
        sleep_seconds = warm.quarantine_hours * _SECONDS_PER_HOUR
        next_state = "quarantine"
    elif result.status == "flood_wait":
        # An unknown flood duration (seconds is None) must NOT collapse to a 0s
        # park that immediately retries the just-flooded account — treat unknown
        # as a full cool-down. A concrete value (including 0) is honoured as-is.
        sleep_seconds = (
            float(result.flood_wait_seconds)
            if result.flood_wait_seconds is not None
            else warm.flood_wait_fallback_hours * _SECONDS_PER_HOUR
        )
        # Human margin: real users don't retry on the exact second a limit lifts.
        sleep_seconds *= 1 + _seams.rng.uniform(0, warm.flood_wait_margin_fraction)
        next_state = "flood_wait"
    elif result.status == "failed":
        sleep_seconds = persona_next_run_seconds(persona, daily_cap, _seams.rng)
        work_actions = (
            result.channels_joined
            + result.channels_read
            + result.reactions_sent
            + result.messages_sent
        )
        if work_actions > 0:
            next_state = "sleeping"
        elif result.last_failed_action == "set_online":
            # A lone presence-flip failure (network/proxy blip on the very first
            # action) is not evidence the account is broken — sleep and retry
            # next cycle instead of parking it in the terminal error column.
            next_state = "sleeping"
        else:
            next_state = "error"
    else:
        # Persona-derived gap: the active window split into the persona's
        # sessions/day (capped by what the phase budget affords), ±jitter.
        sleep_seconds = persona_next_run_seconds(persona, daily_cap, _seams.rng)
        next_state = "sleeping"

    next_run_dt = datetime.now(UTC) + timedelta(seconds=sleep_seconds)
    # peer_flood goes through the quarantine path, so it keeps its raw cooldown;
    # a flood_wait that expires at night is now deferred into the morning window
    # too (a resume at 04:00 is more suspicious than the wait itself).
    if result.status != "peer_flood":
        next_run_dt = _shift_to_active_hours(
            next_run_dt, await _account_tz(account_id), _seams.rng, account_id
        )

    return actions_done, next_run_dt, next_state


async def _resolve_phase_after_cycle(
    account_id: str,
    age_hours: float,
    latest: WarmingStateRecord | None,
) -> tuple[WarmingPhase, str, _PhaseEvent | None]:
    """Compute the post-cycle phase and the ``phase_advanced`` event to emit.

    Returns ``(new_phase, phase_entered_iso, phase_event)``. The event is
    returned rather than logged here so the caller can withhold it when the
    final CAS write is rejected (a newer generation took the row) — a phantom
    ``phase_advanced`` for a state change that never landed would mislead
    diagnosis. We recompute trust on purpose: the cycle may have just shifted
    spam/quarantine/flood signals, and the phase should react in the same write.
    Seed-only semantics for the first ever cycle (``prev is None`` → no event,
    just stamp the entry timestamp).
    """
    post_trust = await account_trust_score(account_id)
    post_intensity = compute_intensity(age_hours, trust_band=post_trust.band)
    new_phase = post_intensity.phase
    prev_phase = latest.current_phase if latest is not None else None
    phase_changed = prev_phase is not None and prev_phase != new_phase
    phase_event: _PhaseEvent | None = None
    if phase_changed and prev_phase is not None:
        direction = (
            "forward"
            if _PHASE_ORDER.index(new_phase) > _PHASE_ORDER.index(prev_phase)
            else "regression"
        )
        phase_event = _PhaseEvent(
            level="INFO" if direction == "forward" else "WARNING",
            extra={
                "from_phase": prev_phase,
                "to_phase": new_phase,
                "direction": direction,
                "trust_score": post_trust.score,
                "cycle_index": (latest.cycles_completed if latest else 0) + 1,
            },
        )
    phase_entered_iso = (
        _now_iso()
        if prev_phase is None or phase_changed
        else (latest.phase_entered_at if latest and latest.phase_entered_at else _now_iso())
    )
    return new_phase, phase_entered_iso, phase_event
