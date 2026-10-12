"""Persisted stint fields carried across a warming restart."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from core.config import settings
from core.logging import log_event
from schemas.lifecycle import LifecycleSnapshot
from schemas.warming import is_warming
from services.lifecycle_journal import record_lifecycle_event
from services.warming.pacing import _now_iso

if TYPE_CHECKING:
    from schemas.warming import ActivityPersona, StartWarmingRequest, WarmingStateRecord


class CarriedStint(NamedTuple):
    """Stable fields that one warming start writes to the account row."""

    started_at: str
    target_days: int
    activity_persona: ActivityPersona
    # The chosen first cycle (``None`` → the loop's cold-start spread).
    first_run_at: str | None
    # A start from idle (or a first start) opens a new stint whose worked days
    # begin at zero; a resume or restart of a running stint keeps them.
    fresh: bool


def carry_or_restamp(
    existing: WarmingStateRecord | None,
    data: StartWarmingRequest,
    first_run_at: str | None = None,
    *,
    resume: bool = False,
) -> CarriedStint:
    """Carry an in-flight stint; restamp a genuine start from idle/stopped.

    A genuine start with a chosen delay begins its stint at the first cycle, not at
    the click: the rest before it must not count toward ``target_days``, or a 1-day
    target behind a 24-hour delay would complete before warming did anything.

    ``resume`` is «Вернуть в прогрев» on a graduated account: the stint goes on with
    its clock and worked days, while the dialog's new length and persona apply — the
    old length is the one the account just finished.
    """
    continuing = existing is not None and is_warming(existing.state)
    resuming = resume and existing is not None and existing.promoted_to_nc
    restamp = first_run_at or _now_iso()
    carried = continuing or resuming
    started_at = existing.started_at if carried and existing and existing.started_at else restamp
    target_days = (
        existing.target_days
        if continuing and existing and existing.target_days
        else (data.target_days or settings.neurocomment.warmed_min_days)
    )
    persona = existing.activity_persona if continuing and existing else data.activity_persona
    return CarriedStint(started_at, target_days, persona, first_run_at, fresh=not carried)


async def journal_start(
    account_id: str,
    existing: WarmingStateRecord | None,
    stint: CarriedStint,
    tg_user_id: int | None,
) -> None:
    """Log and journal a start; ``new_stint`` False = a restart that carried the stint's clock.

    The row is pinned to this start's own stint, not re-read from state: it runs after the
    account lock is released, when a concurrent stop/restart may have stamped a newer one.
    """
    await log_event("INFO", "warming_started", account_id=account_id)
    new_stint = existing is None or existing.started_at != stint.started_at
    snapshot = LifecycleSnapshot(tg_user_id=tg_user_id, stint_started_at=stint.started_at)
    await record_lifecycle_event(account_id, "warming_started", {"new_stint": new_stint}, snapshot)
