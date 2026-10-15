"""Judged lives → counters, cohorts, causes and swimmer rows. Pure: the clock is passed in.

Every count here is per life, so an account warmed in several waves is one account in
every number. The cohort a life falls in is read off its *deciding* wave — the one it
was first released in, else its latest — except for the ``waves`` cut, which groups
lives by how many waves they have had.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from schemas.survival import (
    SURVIVAL_WINDOWS,
    SurvivalCause,
    SurvivalCohort,
    SurvivalCounter,
    SurvivalLife,
    SurvivalWave,
)
from services.survival._outcomes import (
    life_facts,
    life_status,
    window_end,
    window_outcome,
    wounded_by,
)
from services.survival._stints import parse_moment

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime, timedelta

    from schemas.survival import SurvivalCohortKind, SurvivalWindow
    from services.survival._outcomes import LifeFacts
    from services.survival._stints import Life, Stint

_SECONDS_PER_DAY = 86_400
_RELEASE_KINDS = frozenset({"warmed", "promoted"})


@dataclass(frozen=True)
class StintCohorts:
    """What a life is grouped by besides its start week; ``None`` = not known."""

    persona: str | None = None
    target_days: int | None = None
    proxy_country: str | None = None


@dataclass(frozen=True)
class Judged:
    """A life with its facts, its deciding wave's cohort fields and the label its row shows."""

    life: Life
    facts: LifeFacts
    cohorts: StintCohorts
    label: str


@dataclass(frozen=True)
class Clock:
    """``now`` and how early a cached @SpamBot answer may count for a window's end."""

    now: datetime
    grace: timedelta


def _released_in(wave: Stint) -> datetime | None:
    moments = (parse_moment(row.created_at) for row in wave.events if row.kind in _RELEASE_KINDS)
    return min((moment for moment in moments if moment is not None), default=None)


def deciding_wave(life: Life) -> Stint:
    """The wave the life was first released in, else its latest wave."""
    return next((wave for wave in life.waves if _released_in(wave)), life.waves[-1])


def judge(life: Life, cohorts: StintCohorts, label: str) -> Judged:
    """Bundle a life with what was decided about it."""
    return Judged(life=life, facts=life_facts(life), cohorts=cohorts, label=label)


def tally(judged: list[Judged], window: SurvivalWindow, clock: Clock) -> SurvivalCounter:
    """Lives of one window by outcome; a life never released does not enter d7/d30."""
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


def _week_key(item: Judged) -> str:
    year, week, _ = deciding_wave(item.life).started_at.isocalendar()
    return f"{year}-W{week:02d}"


def _length_key(item: Judged) -> str | None:
    days = item.cohorts.target_days
    return str(days) if days is not None else None


_COHORT_KEYS: dict[SurvivalCohortKind, Callable[[Judged], str | None]] = {
    "week": _week_key,
    "waves": lambda item: str(len(item.life.waves)),
    "persona": lambda item: item.cohorts.persona,
    "length": _length_key,
    "proxy_country": lambda item: item.cohorts.proxy_country,
}


def cohort_key(item: Judged, kind: SurvivalCohortKind) -> str | None:
    """The cohort a life falls in: start week, persona, length, country or wave count."""
    return _COHORT_KEYS[kind](item)


def _numeric(kind: SurvivalCohortKind) -> bool:
    return kind in {"length", "waves"}


def cohorts(judged: list[Judged], kind: SurvivalCohortKind, clock: Clock) -> list[SurvivalCohort]:
    """One row per cohort, known keys sorted, the unknown one last."""
    groups: dict[str | None, list[Judged]] = {}
    for item in judged:
        groups.setdefault(cohort_key(item, kind), []).append(item)
    known = sorted(
        (key for key in groups if key is not None),
        key=lambda key: (len(key), key) if _numeric(kind) else (0, key),
    )
    order: list[str | None] = [*known, *([None] if None in groups else [])]
    return [SurvivalCohort(key=key, windows=tally_windows(groups[key], clock)) for key in order]


def causes(judged: list[Judged]) -> list[SurvivalCause]:
    """Deaths by cause, the commonest first."""
    counts = Counter(item.facts.cause for item in judged if item.facts.cause is not None)
    return [SurvivalCause(cause=cause, count=count) for cause, count in counts.most_common()]


def _day(item: Judged, moment: datetime) -> int:
    seconds = (moment - item.life.started_at).total_seconds()
    return max(0, int(seconds // _SECONDS_PER_DAY))


def _waves(item: Judged) -> list[SurvivalWave]:
    rows: list[SurvivalWave] = []
    for wave in item.life.waves:
        released = _released_in(wave)
        rows.append(
            SurvivalWave(
                wave=wave.wave,
                start_day=_day(item, wave.started_at),
                release_day=_day(item, released) if released else None,
            ),
        )
    return rows


def swimmer_row(item: Judged, clock: Clock) -> SurvivalLife:
    """One row of the swimmer plot: the bar to its end, waves, release, death and wounds."""
    facts = item.facts
    end = facts.died_at or facts.censored_at or clock.now
    return SurvivalLife(
        account_id=item.life.account_id,
        label=item.label,
        started_at=item.life.started_at.isoformat(),
        status=life_status(facts, clock.grace),
        end_day=_day(item, end),
        release_day=_day(item, facts.released_at) if facts.released_at else None,
        death_day=_day(item, facts.died_at) if facts.died_at else None,
        found_day=_day(item, facts.found_at) if facts.found_at else None,
        cause=facts.cause,
        wound_days=[_day(item, wound) for wound in facts.wounds],
        waves=_waves(item),
    )
