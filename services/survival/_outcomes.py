"""Stint → what happened to it, and its outcome in each window. Pure: the clock is passed in.

The rules (``schemas.survival``): a death is @SpamBot's ``limited``, a session that died
for good, or a deletion as dead; an old deletion without a reason counts as dead only
when @SpamBot had limited that life, otherwise it is a write-off. A write-off, or a new
stint of the same life, censors the stint: it leaves the count without dying. A peer
flood that @SpamBot did not turn into a restriction is a wound. Alive needs evidence:
a clean @SpamBot answer at or after the window's end, where ``grace`` lets in an answer
up to one cache lifetime early — the probe at the window's end reuses a fresh cached
answer instead of asking again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from schemas.survival import SURVIVAL_WINDOW_DAYS
from services.survival._stints import parse_moment

if TYPE_CHECKING:
    from datetime import datetime

    from schemas.lifecycle import LifecycleEvent
    from schemas.survival import DeathCause, StintStatus, SurvivalOutcome, SurvivalWindow
    from services.survival._stints import Stint

_RELEASE_KINDS = frozenset({"warmed", "promoted"})


@dataclass(frozen=True)
class StintFacts:
    """The moments that decide a stint's outcomes."""

    released_at: datetime | None
    died_at: datetime | None
    cause: DeathCause | None
    # Written off, or a new stint of the same life began — whichever came first.
    censored_at: datetime | None
    wounds: tuple[datetime, ...]
    # Moments of every clean @SpamBot answer, and of any answer at all.
    clean_checks: tuple[datetime, ...]
    checks: tuple[datetime, ...]


def _limited(event: LifecycleEvent) -> bool:
    return event.kind == "spambot_verdict" and event.extra.get("status") == "limited"


def _deletion_is_death(event: LifecycleEvent, life: tuple[LifecycleEvent, ...]) -> bool:
    reason = event.extra.get("reason")
    if reason in {"dead", "retired"}:
        return reason == "dead"
    # Q11: an old deletion without a reason is a death only if @SpamBot limited the life.
    return any(_limited(row) for row in life)


def _ending(stint: Stint) -> tuple[LifecycleEvent | None, DeathCause | None]:
    """The first row that ends the stint and, when it is a death, its cause."""
    for event in stint.events:
        if _limited(event):
            return event, "spam_limited"
        if event.kind == "session_dead":
            return event, "session_dead"
        if event.kind == "deleted":
            dead = _deletion_is_death(event, stint.life_events)
            return event, "deleted_dead" if dead else None
    return None, None


def _moments(events: list[LifecycleEvent]) -> tuple[datetime, ...]:
    moments = (parse_moment(event.created_at) for event in events)
    return tuple(moment for moment in moments if moment is not None)


def stint_facts(stint: Stint) -> StintFacts:
    """Release, death or write-off, wounds and @SpamBot answers of one stint."""
    ending, cause = _ending(stint)
    ended_at = parse_moment(ending.created_at) if ending is not None else None
    # Rows after the stint ended say nothing about it.
    events = list(stint.events)
    if ending is not None:
        events = events[: events.index(ending) + 1]
    releases = _moments([event for event in events if event.kind in _RELEASE_KINDS])
    verdicts = [event for event in events if event.kind == "spambot_verdict"]
    died_at = ended_at if cause is not None else None
    censored_at = ended_at if ending is not None and cause is None else stint.cut_at
    return StintFacts(
        released_at=min(releases) if releases else None,
        died_at=died_at,
        cause=cause,
        censored_at=censored_at if died_at is None else None,
        wounds=_moments([event for event in events if event.kind == "quarantined"]),
        clean_checks=_moments([row for row in verdicts if row.extra.get("status") == "clean"]),
        checks=_moments(verdicts),
    )


def window_end(facts: StintFacts, window: SurvivalWindow) -> datetime | None:
    """When a post-release window closes; ``None`` before the release or for ``warming``."""
    if window == "warming" or facts.released_at is None:
        return None
    return facts.released_at + timedelta(days=SURVIVAL_WINDOW_DAYS[window])


def died_in_warming(facts: StintFacts) -> bool:
    """The death came before (or with) the release, or there was no release at all."""
    if facts.died_at is None:
        return False
    return facts.released_at is None or facts.died_at <= facts.released_at


def _warming_outcome(facts: StintFacts) -> SurvivalOutcome:
    if died_in_warming(facts):
        return "dead"
    if facts.released_at is not None:
        return "alive"
    return "censored" if facts.censored_at is not None else "pending"


def _closing_outcome(
    facts: StintFacts,
    end: datetime,
    now: datetime,
    grace: timedelta,
) -> SurvivalOutcome:
    if facts.died_at is not None and facts.died_at <= end:
        return "dead"
    if facts.censored_at is not None and facts.censored_at <= end:
        return "censored"
    if now < end:
        return "pending"
    return "alive" if any(moment >= end - grace for moment in facts.clean_checks) else "unchecked"


def window_outcome(
    facts: StintFacts,
    window: SurvivalWindow,
    now: datetime,
    grace: timedelta,
) -> SurvivalOutcome | None:
    """The stint's outcome in ``window``; ``None`` when the stint was never released.

    Only released stints enter the 7- and 30-day windows: a death during warming is
    counted once, in the warming window.
    """
    if window == "warming":
        return _warming_outcome(facts)
    end = window_end(facts, window)
    if end is None or died_in_warming(facts):
        return None
    return _closing_outcome(facts, end, now, grace)


def wounded_by(facts: StintFacts, moment: datetime | None) -> bool:
    """A wound before ``moment`` (``None``: at any time)."""
    return any(moment is None or wound <= moment for wound in facts.wounds)


def stint_status(facts: StintFacts, grace: timedelta) -> StintStatus:
    """Where the stint stands now — the card badge and the swimmer row's colour."""
    if facts.died_at is not None:
        return "dead"
    if facts.censored_at is not None:
        return "censored"
    if facts.released_at is None:
        return "warming"
    if not any(moment >= facts.released_at - grace for moment in facts.clean_checks):
        return "unchecked"
    return "wounded" if facts.wounds else "alive"
