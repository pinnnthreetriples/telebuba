"""What kind of visit this is and in which order it goes (Прогрев 2.0, item 4.4).

Four kinds: «Глянул» (one channel, two or three actions), «Обычный», «Залип» (more
channels, a longer stay, twice the budget; never in the first days) and «Только
ответить» (opened from a notification to answer a partner's DM). The kind is drawn
with the persona's weights once the account is about to go online; its budget caps
the visit (``min(remaining, kind budget)``) and whatever it leaves unspent returns
through the reservation's reconcile (#208).

Within a visit the order varies inside fixed rules: the story glance and one free read
extra may come before the channels; nothing that writes ever does. The visit's step
list is stored so the card's progress follows this visit's own order and never jumps
back. Pure apart from the caller's rng.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from core.config import settings
from schemas._warming_visits import VisitPlan
from services.warming._phases import _PHASE_ORDER

if TYPE_CHECKING:
    from random import Random

    from schemas._warming_visits import VisitKind, VisitStep
    from schemas.warming import WarmingPhase

_CHANNEL_STEPS: tuple[VisitStep, ...] = ("join", "read", "react")
_DEFAULT_WEIGHTS = {"normal": 1.0}


def _kind_weights(persona: str, phase: WarmingPhase, *, dm_waiting: bool) -> dict[str, float]:
    warm = settings.warming
    weights = dict(warm.persona_visit_weights.get(persona) or _DEFAULT_WEIGHTS)
    if _PHASE_ORDER.index(phase) < _PHASE_ORDER.index(warm.visit_binge_min_phase):
        weights.pop("binge", None)
    if dm_waiting:
        weights["reply"] = warm.visit_reply_only_weight
    return weights


def choose_visit_kind(
    persona: str,
    phase: WarmingPhase,
    rng: Random,
    *,
    dm_waiting: bool,
) -> VisitKind:
    """Draw the kind with the persona's weights; a normal visit when nothing weighs."""
    weights = _kind_weights(persona, phase, dm_waiting=dm_waiting)
    order: tuple[VisitKind, ...] = ("normal", "glance", "binge", "reply")
    total = sum(weights.get(kind, 0.0) for kind in order)
    if total <= 0:
        return "normal"
    point = rng.random() * total
    for kind in order:
        point -= weights.get(kind, 0.0)
        if point < 0:
            return kind
    return "normal"


def _steps(*, stories_first: bool, read_extra_first: bool) -> list[VisitStep]:
    steps: list[VisitStep] = ["set_online"]
    if read_extra_first:
        steps.append("extras")
    if stories_first:
        steps.append("stories")
    steps.extend(_CHANNEL_STEPS)
    if not stories_first:
        steps.append("stories")
    steps.append("send_dm")
    if not read_extra_first:
        steps.append("extras")
    return steps


def plan_visit(kind: VisitKind, rng: Random) -> VisitPlan:
    """The steps, channels and order of one visit of ``kind``."""
    warm = settings.warming
    if kind == "reply":
        return VisitPlan(kind=kind, steps=["set_online", "send_dm"], channels=0)
    if kind == "glance":
        return VisitPlan(kind=kind, steps=["set_online", *_CHANNEL_STEPS], channels=1)
    stories_first = rng.random() < warm.visit_stories_first_probability
    read_extra_first = rng.random() < warm.visit_read_extra_first_probability
    plan = VisitPlan(
        kind=kind,
        steps=_steps(stories_first=stories_first, read_extra_first=read_extra_first),
        stories_first=stories_first,
        read_extra_first=read_extra_first,
    )
    if kind == "binge":
        plan.channels = rng.randint(*warm.visit_binge_channels)
        plan.linger_seconds = rng.uniform(*warm.visit_binge_linger_seconds)
    return plan


def kind_budget(
    kind: VisitKind,
    visit_budget: int | None,
    remaining: int | None,
    rng: Random,
) -> int | None:
    """Actions this visit may spend: its kind's share, never past the day's remainder."""
    warm = settings.warming
    if kind == "glance":
        budget: int | None = rng.randint(*warm.visit_glance_actions)
    elif kind == "reply":
        # set_online plus the one DM turn; the read-ack rides inside the turn.
        budget = 2
    elif kind == "binge" and visit_budget is not None:
        budget = math.ceil(visit_budget * warm.visit_binge_budget_factor)
    else:
        budget = visit_budget
    if budget is None:
        return remaining
    return budget if remaining is None else min(remaining, budget)
