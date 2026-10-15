"""How often an account opens the app and what one visit may spend (Прогрев 2.0, item 4.3).

The persona decides how many visits a day; the phase cap only bounds them by the
smallest worthwhile visit (``visit_actions_range``), and the day's budget is split
between the visits instead of one visit taking all of it. A cap of 15 therefore makes
5-6 short visits rather than 3 long ones.

Pure (config + the caller's rng). ``cadence_preview`` is the same math for the start
dialog's hint, so the numbers it shows are the numbers the engine uses.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from core.config import settings
from schemas._warming_visits import CadencePreview
from services.warming._tuning import effective_tuning

if TYPE_CHECKING:
    from collections.abc import Mapping
    from random import Random

    from schemas._warming_visits import Persona, WarmingPhase

# A visit always spends one action on the SetOnline presence flip; it needs room for
# at least one real action beyond it, or it would burn an inter-visit gap on nothing.
MIN_VISIT_ACTIONS = 2

_PERSONAS: tuple[Persona, ...] = ("calm", "normal", "active")
_PHASES: tuple[WarmingPhase, ...] = ("intro", "settling", "warming", "active", "warmed")


def _affordable(daily_cap: int, per_visit: float) -> int:
    return max(1, round(daily_cap / per_visit))


def persona_visits(persona: str, daily_cap: int, rng: Random) -> int:
    """Today's visits: the persona's draw, bounded by what the cap affords."""
    low, high = settings.warming.persona_sessions[persona]
    draw = rng.randint(low, high)
    if daily_cap <= 0:
        return max(1, draw)
    per_visit = rng.uniform(*settings.warming.visit_actions_range)
    return max(1, min(draw, _affordable(daily_cap, per_visit)))


def expected_visits(persona: str, daily_cap: int) -> float:
    """The mean of ``persona_visits`` — what the budget split plans with."""
    low, high = settings.warming.persona_sessions[persona]
    mean_draw = (low + high) / 2
    if daily_cap <= 0:
        return max(1.0, mean_draw)
    lo, hi = settings.warming.visit_actions_range
    return max(1.0, min(mean_draw, daily_cap / ((lo + hi) / 2)))


def visit_budget(persona: str, daily_cap: int, remaining: int | None) -> int | None:
    """Actions this visit may spend: the day's remainder split over the visits left.

    The visits left are read off the share of the budget still unspent, so a day that
    started late still splits evenly. Never below the two-action floor, never above
    what is left; ``None`` (no cap) stays unbounded.
    """
    if remaining is None or daily_cap <= 0:
        return remaining
    visits_left = max(1.0, expected_visits(persona, daily_cap) * remaining / daily_cap)
    budget = max(MIN_VISIT_ACTIONS, math.ceil(remaining / visits_left))
    return min(remaining, budget)


def cadence_preview(daily_caps: Mapping[str, int] | None = None) -> list[CadencePreview]:
    """Visits a day and actions per visit for every persona in every phase.

    Under the pace in force (``_tuning``), or under ``daily_caps`` when the pace card
    shows what a preset would make.
    """
    warm = settings.warming
    caps = daily_caps if daily_caps is not None else effective_tuning().phase_daily_cap
    lo, hi = warm.visit_actions_range
    rows: list[CadencePreview] = []
    for persona in _PERSONAS:
        low, high = warm.persona_sessions[persona]
        for phase in _PHASES:
            cap = caps[phase]
            visits_min = max(1, min(low, _affordable(cap, hi)))
            visits_max = max(visits_min, min(high, _affordable(cap, lo)))
            per_visit = math.ceil(cap / expected_visits(persona, cap))
            rows.append(
                CadencePreview(
                    persona=persona,
                    phase=phase,
                    visits_min=visits_min,
                    visits_max=visits_max,
                    actions_per_visit=max(MIN_VISIT_ACTIONS, per_visit),
                ),
            )
    return rows
