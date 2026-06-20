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
from services.trust import account_trust_score
from services.warming import _seams
from services.warming.pacing import (
    _PHASE_ORDER,
    _SECONDS_PER_HOUR,
    _account_tz,
    _now_iso,
    _shift_to_active_hours,
    compute_intensity,
)

if TYPE_CHECKING:
    from schemas.logs import LogLevel
    from schemas.warming import (
        WarmingCycleResult,
        WarmingPhase,
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
        # An unknown flood duration (seconds is None) must NOT collapse to a 0s
        # park that immediately retries the just-flooded account — treat unknown
        # as a full cool-down. A concrete value (including 0) is honoured as-is.
        sleep_seconds = (
            float(result.flood_wait_seconds)
            if result.flood_wait_seconds is not None
            else warm.cycle_sleep_min_hours * _SECONDS_PER_HOUR
        )
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
        sleep_seconds = _seams.rng.uniform(
            warm.cycle_sleep_min_hours * _SECONDS_PER_HOUR,
            warm.cycle_sleep_max_hours * _SECONDS_PER_HOUR,
        )
        next_state = "sleeping"

    next_run_dt = datetime.now(UTC) + timedelta(seconds=sleep_seconds)
    if result.status not in {"peer_flood", "flood_wait"}:
        next_run_dt = _shift_to_active_hours(next_run_dt, await _account_tz(account_id))

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
