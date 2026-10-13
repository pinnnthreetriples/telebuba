"""Judged stints → counters, cohorts, causes and swimmer rows. Pure: the clock is passed in."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from schemas.survival import (
    SURVIVAL_WINDOWS,
    SurvivalCause,
    SurvivalCohort,
    SurvivalCounter,
    SurvivalStint,
)
from services.survival._outcomes import (
    stint_facts,
    stint_status,
    window_end,
    window_outcome,
    wounded_by,
)

if TYPE_CHECKING:
    from datetime import datetime, timedelta

    from schemas.survival import SurvivalCohortKind, SurvivalWindow
    from services.survival._outcomes import StintFacts
    from services.survival._stints import Stint

_SECONDS_PER_DAY = 86_400


@dataclass(frozen=True)
class StintCohorts:
    """What a stint is grouped by besides its start week; ``None`` = not known."""

    persona: str | None = None
    target_days: int | None = None
    proxy_country: str | None = None


@dataclass(frozen=True)
class Judged:
    """A stint with its facts, its cohort fields and the label its row shows."""

    stint: Stint
    facts: StintFacts
    cohorts: StintCohorts
    label: str


@dataclass(frozen=True)
class Clock:
    """``now`` and how early a cached @SpamBot answer may count for a window's end."""

    now: datetime
    grace: timedelta


def judge(stint: Stint, cohorts: StintCohorts, label: str) -> Judged:
    """Bundle a stint with what was decided about it."""
    return Judged(stint=stint, facts=stint_facts(stint), cohorts=cohorts, label=label)


def tally(judged: list[Judged], window: SurvivalWindow, clock: Clock) -> SurvivalCounter:
    """Stints of one window by outcome; a stint never released does not enter d7/d30."""
    counts: Counter[str] = Counter()
    for item in judged:
        outcome = window_outcome(item.facts, window, clock.now, clock.grace)
        if outcome is None:
            continue
        counts[outcome] += 1
        horizon = item.facts.released_at if window == "warming" else window_end(item.facts, window)
        if outcome == "alive" and wounded_by(item.facts, horizon):
            counts["wounded"] += 1
    return SurvivalCounter(window=window, **counts)


def tally_windows(judged: list[Judged], clock: Clock) -> list[SurvivalCounter]:
    """One counter per window, in ``SURVIVAL_WINDOWS`` order."""
    return [tally(judged, window, clock) for window in SURVIVAL_WINDOWS]


def cohort_key(item: Judged, kind: SurvivalCohortKind) -> str | None:
    """The cohort a stint falls in: ISO start week, persona, length in days, or country."""
    if kind == "week":
        year, week, _ = item.stint.started_at.isocalendar()
        return f"{year}-W{week:02d}"
    if kind == "persona":
        return item.cohorts.persona
    if kind == "length":
        days = item.cohorts.target_days
        return str(days) if days is not None else None
    return item.cohorts.proxy_country


def cohorts(judged: list[Judged], kind: SurvivalCohortKind, clock: Clock) -> list[SurvivalCohort]:
    """One row per cohort, known keys sorted, the unknown one last."""
    groups: dict[str | None, list[Judged]] = {}
    for item in judged:
        groups.setdefault(cohort_key(item, kind), []).append(item)
    known = sorted(
        (key for key in groups if key is not None),
        key=lambda key: (len(key), key) if kind == "length" else (0, key),
    )
    order: list[str | None] = [*known, *([None] if None in groups else [])]
    return [SurvivalCohort(key=key, windows=tally_windows(groups[key], clock)) for key in order]


def causes(judged: list[Judged]) -> list[SurvivalCause]:
    """Deaths by cause, the commonest first."""
    counts = Counter(item.facts.cause for item in judged if item.facts.cause is not None)
    return [SurvivalCause(cause=cause, count=count) for cause, count in counts.most_common()]


def _day(item: Judged, moment: datetime) -> int:
    seconds = (moment - item.stint.started_at).total_seconds()
    return max(0, int(seconds // _SECONDS_PER_DAY))


def swimmer_row(item: Judged, clock: Clock) -> SurvivalStint:
    """One row of the swimmer plot: the bar to its end, release, death and wounds."""
    facts = item.facts
    end = facts.died_at or facts.censored_at or clock.now
    return SurvivalStint(
        account_id=item.stint.account_id,
        label=item.label,
        started_at=item.stint.started_at.isoformat(),
        status=stint_status(facts, clock.grace),
        end_day=_day(item, end),
        release_day=_day(item, facts.released_at) if facts.released_at else None,
        death_day=_day(item, facts.died_at) if facts.died_at else None,
        cause=facts.cause,
        wound_days=[_day(item, wound) for wound in facts.wounds],
    )
