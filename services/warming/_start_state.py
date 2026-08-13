"""Persisted stint fields carried across a warming restart."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from core.config import settings
from schemas.warming import is_warming
from services.warming.pacing import _now_iso

if TYPE_CHECKING:
    from schemas.warming import ActivityPersona, StartWarmingRequest, WarmingStateRecord


class CarriedStint(NamedTuple):
    """Stable fields that one warming start writes to the account row."""

    started_at: str
    target_days: int
    activity_persona: ActivityPersona


def carry_or_restamp(
    existing: WarmingStateRecord | None,
    data: StartWarmingRequest,
) -> CarriedStint:
    """Carry an in-flight stint; restamp a genuine start from idle/stopped."""
    continuing = existing is not None and is_warming(existing.state)
    started_at = existing.started_at if continuing and existing.started_at else _now_iso()
    target_days = (
        existing.target_days
        if continuing and existing.target_days
        else (data.target_days or settings.neurocomment.warmed_min_days)
    )
    persona = existing.activity_persona if continuing else data.activity_persona
    return CarriedStint(started_at, target_days, persona)
