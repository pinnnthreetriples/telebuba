"""Visits of a warming account — cadence and visit types (Прогрев 2.0, items 4.3-4.4).

Split from ``schemas.warming`` for the file-size budget. Data contract only.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ``schemas.warming.WarmingPhase`` / ``ActivityPersona``, spelled again here because
# that module imports this one; ``tests/services/warming/test_cadence.py`` pins them equal.
WarmingPhase = Literal["intro", "settling", "warming", "active", "warmed"]
Persona = Literal["calm", "normal", "active"]

# How the account opens the app this time: a normal visit, a glance (1 channel, a
# couple of actions), a binge (more channels, lingers), or only to answer a DM.
VisitKind = Literal["normal", "glance", "binge", "reply"]

# One progress token per step a visit may take, in the order it takes them.
VisitStep = Literal["set_online", "join", "read", "react", "stories", "send_dm", "extras"]


class VisitPlan(BaseModel):
    """What this visit does and in which order; the card's progress follows ``steps``."""

    kind: VisitKind = "normal"
    steps: list[VisitStep] = Field(default_factory=list)
    # Channels to walk, ``None`` = the configured per-cycle range.
    channels: int | None = Field(default=None, ge=0)
    stories_first: bool = False
    # One free read extra before the channels.
    read_extra_first: bool = False
    # Extra pause at the end of the channel walk (a binge lingers), seconds.
    linger_seconds: float = Field(default=0.0, ge=0.0)


class CycleMarks(BaseModel):
    """What a booked cycle shows on its card, written with its booking (item 5.1).

    ``base_actions`` is the day's settled count the booking sits on (``_console``);
    the visit's kind and step order drive the card's progress rail.
    """

    base_actions: int = Field(ge=0)
    visit_kind: VisitKind
    steps: list[VisitStep] = Field(default_factory=list)


class CadencePreview(BaseModel):
    """Real visit numbers for one persona in one phase, for the start dialog's hint."""

    persona: Persona
    phase: WarmingPhase
    visits_min: int = Field(ge=1)
    visits_max: int = Field(ge=1)
    # About how many actions one visit gets (set_online included).
    actions_per_visit: int = Field(ge=1)
