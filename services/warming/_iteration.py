"""One warming iteration: eligibility, reservation, and cycle execution."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, NamedTuple

from core.logging import log_event
from core.repositories.dialogues import oldest_unreplied_for
from core.repositories.spam_status import get_spam_status
from core.repositories.warming import load_warming_settings
from core.repositories.warming_marks import store_cycle_progress
from schemas._warming_visits import CycleMarks, VisitPlan
from schemas.warming import WarmingCycleRequest, WarmingCycleResult, spam_block_known
from services.warming import _loop, _seams
from services.warming._cadence import visit_budget
from services.warming._local_day import _local_date
from services.warming._quarantine import _recover_from_quarantine
from services.warming._reservation import _release_reservation_on_exit, _Reservation
from services.warming._snapshot import (
    current_snapshot,
    load_cycle_snapshot,
    publish_snapshot,
    snapshot_scope,
)
from services.warming._spam_check import _spam_step
from services.warming._state import READ_CURRENT
from services.warming._steps import _ChannelTally
from services.warming._transitions import (
    _gate_readiness,
    _gate_target_reached,
    _matches_active_run,
)
from services.warming._visits import choose_visit_kind, kind_budget, plan_visit
from services.warming._worked_days import _count_cycle_day
from services.warming.pacing import _account_age_hours, _now_iso, _roll_daily, compute_intensity

if TYPE_CHECKING:
    from collections.abc import Sequence

    from schemas.spam_status import SpamStatusVerdict
    from schemas.warming import ActivityPersona, WarmingPhase, WarmingStateRecord

logger = logging.getLogger(__name__)

# What a phone visit does in the app (``_mobile_cycle``): it never joins, DMs or runs extras.
_PHONE_STEPS = frozenset({"set_online", "stories", "read", "react"})


class _IterationPlan(NamedTuple):
    age_hours: float
    effective_cap: int
    daily: tuple[int, str]
    remaining: int | None
    dm_allowed: bool
    persona: ActivityPersona
    phase: WarmingPhase = "intro"


class _ReservedIteration(NamedTuple):
    age_hours: float
    effective_cap: int
    remaining: int | None
    dm_allowed: bool
    persona: ActivityPersona
    reservation: _Reservation
    phase: WarmingPhase = "intro"
    # This visit's kind and order and its share of the day's budget (4.3/4.4).
    visit: VisitPlan | None = None
    budget: int | None = None


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


async def _gate_spam(
    account_id: str,
    record: WarmingStateRecord | None,
    now: datetime,
    run_id: str | None,
) -> WarmingCycleResult | SpamStatusVerdict | None:
    """The spam step on the cached reading; returns the reading the cycle goes on with."""
    spam = await get_spam_status(account_id)
    if record is None:
        return spam
    gated, asked = await _spam_step(account_id, record, spam, now, run_id=run_id)
    if gated is not None:
        return gated
    # @SpamBot was asked, so the stored reading moved on: score trust from that one.
    return await get_spam_status(account_id) if asked else spam


async def _plan_iteration(
    account_id: str,
    run_id: str | None,
) -> _IterationPlan | WarmingCycleResult:
    """Apply pre-cycle gates and compute the account's available budget.

    Past the generation, target and spam gates the rest of the cycle's data is read
    once (``_snapshot``) and published for the steps that follow.
    """
    now = datetime.now(UTC)
    controls = await load_warming_settings()
    record = await _loop.fetch_warming_state(account_id)
    initial_gate = await _gate_initial_state(account_id, record, now, run_id)
    if initial_gate is not None:
        return initial_gate
    spam = await _gate_spam(account_id, record, now, run_id)
    if isinstance(spam, WarmingCycleResult):
        return spam

    snapshot = await load_cycle_snapshot(account_id, controls, record, spam)
    publish_snapshot(snapshot)
    account = snapshot.account
    age_hours = _account_age_hours(account, now)
    trust = snapshot.trust(now)
    not_ready = await _gate_readiness(account, controls, record, trust, now, run_id=run_id)
    if not_ready is not None:
        return not_ready
    intensity = compute_intensity(
        age_hours,
        trust_band=trust.band,
        recovered_at=record.recovered_at if record is not None else None,
        now=now,
        started_at=record.started_at if record is not None else None,
        registered_at=account.telegram_registered_at if account is not None else None,
    )
    daily = _roll_daily(record, _local_date(await _loop._account_tz(account_id), now))  # noqa: SLF001
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
        # A confirmed spam block shuts DMs while it stands: the account cannot write to
        # strangers. A clean answer this cycle's spam step just read lifts it already.
        intensity.dm_allowed and not spam_block_known(record, spam),
        persona,
        intensity.phase,
    )


async def _plan_this_visit(
    account_id: str,
    plan: _IterationPlan,
) -> tuple[VisitPlan, int | None]:
    """This visit's kind, order and budget, booked with the reservation for the card.

    The budget is the day's remainder split over the visits left (4.3), capped by the
    kind's share (4.4); what the visit leaves unspent returns through the reconcile.
    «Только ответить» is offered only while a partner's DM waits and DMs are allowed,
    never to a phone, which never writes one; a phone visit's card shows only the steps
    the app takes.
    """
    phone = _seams.bound_device() == "mobile"
    dm_waiting = (
        plan.dm_allowed and not phone and await oldest_unreplied_for(account_id) is not None
    )
    kind = choose_visit_kind(plan.persona, plan.phase, _seams.rng, dm_waiting=dm_waiting)
    visit = plan_visit(kind, _seams.rng)
    if phone:
        visit.steps = [step for step in visit.steps if step in _PHONE_STEPS]
    split = visit_budget(plan.persona, plan.effective_cap, plan.remaining)
    return visit, kind_budget(kind, split, plan.remaining, _seams.rng)


async def _reserve_iteration(
    account_id: str,
    plan: _IterationPlan,
    run_id: str | None,
) -> _ReservedIteration | WarmingCycleResult:
    """Plan the visit, then reserve the full remaining daily budget before any Telegram action.

    One write books the budget and stores what the card shows while it is booked: the
    settled count the booking sits on (``_console``) and the visit's plan. Both ride
    the booking's own CAS, so an older cycle's marks never land on a newer booking.
    """
    visit, budget = await _plan_this_visit(account_id, plan)
    reservation = _Reservation.book(plan.daily, plan.remaining)
    snapshot = current_snapshot(account_id)
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
        cycle_marks=CycleMarks(
            base_actions=reservation.daily_count, visit_kind=visit.kind, steps=visit.steps
        ),
        # The gates just read the row and nothing since wrote what this write carries.
        current=snapshot.record if snapshot is not None else READ_CURRENT,
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
        plan.phase,
        visit,
        budget,
    )


class _ProgressReporter:
    """Best-effort monotonic progress rail for one active cycle."""

    def __init__(
        self,
        account_id: str,
        run_id: str | None,
        steps: Sequence[str] | None = None,
    ) -> None:
        self.account_id = account_id
        self.run_id = run_id
        # This visit's own order (4.4), else the classic one; set_online comes first.
        self.steps = tuple(steps) if steps else _loop._PROGRESS_STEPS  # noqa: SLF001
        self.max_step = 0  # set_online is seeded by the reservation write.

    async def __call__(self, step: str) -> None:
        idx = self.steps.index(step) if step in self.steps else -1
        if idx <= self.max_step:
            return
        self.max_step = idx
        try:
            # One narrow UPDATE: the rail needs no read of the row to carry the rest.
            await store_cycle_progress(self.account_id, step, _now_iso(), self.run_id)
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
    visit = reserved.visit or VisitPlan()
    try:
        result = await _loop._execute_cycle(  # noqa: SLF001 - patchable cycle seam.
            WarmingCycleRequest(
                account_id=account_id,
                remaining_actions=reserved.budget,
                dm_allowed=reserved.dm_allowed,
                activity_persona=reserved.persona,
                visit=visit,
            ),
            on_step=_ProgressReporter(account_id, run_id, visit.steps),
            tally=tally,
        )
        schedule = await _loop._schedule_next_run(  # noqa: SLF001 - patchable schedule seam.
            account_id,
            result,
            reserved.persona,
            reserved.effective_cap,
        )
        settled = await _loop._finalize_after_cycle(  # noqa: SLF001 - patchable settle seam.
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
    # After the settle, outside the reservation guard: the budget is already
    # reconciled, and the count is fenced by the same generation as the settle.
    await _count_cycle_day(account_id, settled, reserved.reservation.daily_date, run_id=run_id)
    return settled


async def run_loop_iteration(
    account_id: str,
    *,
    run_id: str | None = None,
) -> WarmingCycleResult:
    """Run one CAS-fenced warming iteration without the inter-cycle sleep.

    The cycle's data is read once and lives for exactly this iteration
    (``_snapshot.snapshot_scope``), so the next iteration starts from fresh reads.
    """
    with snapshot_scope():
        planned = await _plan_iteration(account_id, run_id)
        if isinstance(planned, WarmingCycleResult):
            return planned
        reserved = await _reserve_iteration(account_id, planned, run_id)
        if isinstance(reserved, WarmingCycleResult):
            return reserved
        return await _run_reserved_cycle(account_id, reserved, run_id)
