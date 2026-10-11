"""Persisted stint fields carried across a warming restart."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from core.config import settings
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


def carry_or_restamp(
    existing: WarmingStateRecord | None,
    data: StartWarmingRequest,
    first_run_at: str | None = None,
) -> CarriedStint:
    """Carry an in-flight stint; restamp a genuine start from idle/stopped.

    A genuine start with a chosen delay begins its stint at the first cycle, not at
    the click: the rest before it must not count toward ``target_days``, or a 1-day
    target behind a 24-hour delay would complete before warming did anything.
    """
    continuing = existing is not None and is_warming(existing.state)
    restamp = first_run_at or _now_iso()
    started_at = existing.started_at if continuing and existing.started_at else restamp
    target_days = (
        existing.target_days
        if continuing and existing.target_days
        else (data.target_days or settings.neurocomment.warmed_min_days)
    )
    persona = existing.activity_persona if continuing else data.activity_persona
    return CarriedStint(started_at, target_days, persona, first_run_at)


async def journal_start(
    account_id: str,
    existing: WarmingStateRecord | None,
    stint: CarriedStint,
) -> None:
    """Journal a start; ``new_stint`` False = a restart that carried the stint's clock."""
    new_stint = existing is None or existing.started_at != stint.started_at
    await record_lifecycle_event(account_id, "warming_started", {"new_stint": new_stint})
