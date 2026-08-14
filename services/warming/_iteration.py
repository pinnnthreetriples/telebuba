"""One warming iteration: eligibility, reservation, and cycle execution."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, NamedTuple

from core.db import fetch_account, load_warming_settings
from core.logging import log_event
from schemas.warming import WarmingCycleRequest, WarmingCycleResult
from services.trust import account_trust_score
from services.warming import _loop, _seams
from services.warming._quarantine import _recover_from_quarantine
from services.warming._reservation import _release_reservation_on_exit, _Reservation
from services.warming._steps import _ChannelTally
from services.warming._transitions import (
    _gate_readiness,
    _gate_target_reached,
    _matches_active_run,
)
from services.warming.pacing import _account_age_hours, _now_iso, _roll_daily, compute_intensity

if TYPE_CHECKING:
    from schemas.warming import ActivityPersona, WarmingStateRecord

logger = logging.getLogger(__name__)


class _IterationPlan(NamedTuple):
    age_hours: float
    effective_cap: int
    daily: tuple[int, str]
    remaining: int | None
    dm_allowed: bool
    persona: ActivityPersona


class _ReservedIteration(NamedTuple):
    age_hours: float
    effective_cap: int
    remaining: int | None
    dm_allowed: bool
    persona: ActivityPersona
    reservation: _Reservation


async def _gate_initial_state(
    account_id: str,
    record: WarmingStateRecord | None,
    now: datetime,
    run_id: str | None,
) -> WarmingCycleResult | None:
    """Apply generation, target, and quarantine gates in lifecycle order."""
    if not _matches_active_run(record, run_id):
        return WarmingCycleResult(account_id=account_id, status="skipped", detail="stale run")
    done = await _gate_target_reached(account_id, record, now, run_id=run_id)
    if done is not None:
        return done
    if record is not None and record.state == "quarantine":
        return await _recover_from_quarantine(account_id, record, now, run_id=run_id)
    return None


async def _plan_iteration(
    account_id: str,
    run_id: str | None,
) -> _IterationPlan | WarmingCycleResult:
    """Apply pre-cycle gates and compute the account's available budget."""
    now = datetime.now(UTC)
    controls = await load_warming_settings()
    record = await _loop.fetch_warming_state(account_id)
    initial_gate = await _gate_initial_state(account_id, record, now, run_id)
    if initial_gate is not None:
        return initial_gate

    account = await fetch_account(account_id)
    age_hours = _account_age_hours(account, now)
    trust = await account_trust_score(account_id)
    not_ready = await _gate_readiness(account, controls, record, trust, now, run_id=run_id)
    if not_ready is not None:
        return not_ready
    intensity = compute_intensity(age_hours, trust_band=trust.band)
    daily = _roll_daily(record, now.date().isoformat())
    quiet = await _loop._gate_quiet_day(account_id, daily, now, run_id=run_id)  # noqa: SLF001
    if quiet is not None:
        return quiet
    gated = await _loop._gate_daily_limit(  # noqa: SLF001
        account_id,
        intensity.daily_cap,
        daily,
        now,
        run_id=run_id,
    )
    if gated is not None:
        return gated
    remaining = max(0, intensity.daily_cap - daily[0]) if intensity.daily_cap > 0 else None
    persona = record.activity_persona if record is not None else "normal"
    return _IterationPlan(
        age_hours,
        intensity.daily_cap,
        daily,
        remaining,
        intensity.dm_allowed,
        persona,
    )


async def _reserve_iteration(
    account_id: str,
    plan: _IterationPlan,
    run_id: str | None,
) -> _ReservedIteration | WarmingCycleResult:
    """Reserve the full remaining daily budget before any Telegram action."""
    reservation = _Reservation.book(plan.daily, plan.remaining)
    started = await _loop._set_state(  # noqa: SLF001 - patchable state seam.
        account_id,
        "active",
        last_event="cycle_started",
        heartbeat_at=datetime.now(UTC).isoformat(),
        last_error=None,
        last_action="set_online",
        last_channel=None,
        daily_actions=reservation.booked,
        daily_count_date=plan.daily[1],
        expected_run_id=run_id,
        reservation_token=reservation.token,
    )
    if run_id is not None and not started.applied:
        return WarmingCycleResult(account_id=account_id, status="skipped", detail="stale run")
    return _ReservedIteration(
        plan.age_hours,
        plan.effective_cap,
        plan.remaining,
        plan.dm_allowed,
        plan.persona,
        reservation,
    )


class _ProgressReporter:
    """Best-effort monotonic progress rail for one active cycle."""

    def __init__(self, account_id: str, run_id: str | None) -> None:
        self.account_id = account_id
        self.run_id = run_id
        self.max_step = 0  # set_online is seeded by the reservation write.

    async def __call__(self, step: str) -> None:
        idx = _loop._PROGRESS_STEPS.index(step) if step in _loop._PROGRESS_STEPS else -1  # noqa: SLF001
        if idx <= self.max_step:
            return
        self.max_step = idx
        try:
            await _loop._set_state(  # noqa: SLF001 - patchable state seam.
                self.account_id,
                "active",
                last_action=step,
                heartbeat_at=_now_iso(),
                expected_run_id=self.run_id,
            )
        except Exception as exc:  # Cosmetic progress must not abort a healthy cycle.
            logger.exception("progress write failed for %s at step %s", self.account_id, step)
            await log_event(
                "WARNING",
                "warming_progress_write_failed",
                account_id=self.account_id,
                extra={"step": step, "error_type": type(exc).__name__},
            )


async def _run_reserved_cycle(
    account_id: str,
    reserved: _ReservedIteration,
    run_id: str | None,
) -> WarmingCycleResult:
    """Run and settle one reserved cycle, releasing budget on local failure."""
    tally = _ChannelTally()
    try:
        async with _loop._cycle_semaphore:  # noqa: SLF001 - fleet-wide mutable test seam.
            result = await _loop._execute_cycle(  # noqa: SLF001 - patchable cycle seam.
                WarmingCycleRequest(
                    account_id=account_id,
                    remaining_actions=reserved.remaining,
                    dm_allowed=reserved.dm_allowed,
                    activity_persona=reserved.persona,
                ),
                on_step=_ProgressReporter(account_id, run_id),
                tally=tally,
            )
        schedule = await _loop._schedule_next_run(  # noqa: SLF001 - patchable schedule seam.
            account_id,
            result,
            reserved.persona,
            reserved.effective_cap,
        )
        return await _loop._finalize_after_cycle(  # noqa: SLF001 - patchable settle seam.
            account_id,
            result,
            reserved.age_hours,
            reserved.reservation,
            schedule,
            run_id=run_id,
        )
    except _seams.WarmingLeaseLostMidDispatchError:
        # The request was already outside the process when the lease went, so what
        # Telegram did with it is unknowable — the conservative reading is that the
        # whole remaining budget went with it, and the reservation stays booked.
        # Only THIS fence earns the exemption: its sibling raises before dispatch,
        # where nothing was sent and keeping the day booked would park the account on
        # a limit it never spent — the very forfeit ``_reservation`` exists to prevent.
        raise
    except BaseException:
        await _release_reservation_on_exit(account_id, reserved.reservation, tally.attempts)
        raise


async def run_loop_iteration(
    account_id: str,
    *,
    run_id: str | None = None,
) -> WarmingCycleResult:
    """Run one CAS-fenced warming iteration without the inter-cycle sleep."""
    planned = await _plan_iteration(account_id, run_id)
    if isinstance(planned, WarmingCycleResult):
        return planned
    reserved = await _reserve_iteration(account_id, planned, run_id)
    if isinstance(reserved, WarmingCycleResult):
        return reserved
    return await _run_reserved_cycle(account_id, reserved, run_id)
