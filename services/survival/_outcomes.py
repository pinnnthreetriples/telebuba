"""Life → what happened to it, and its outcome in each window. Pure: the clock is passed in.

The unit is the account life, not the wave, so a re-warmed account is counted once
(``schemas.survival`` states the rules):

* release — the life's first graduation (target reached or the operator's promotion);
  every window is measured from it, whatever wave it came in;
* death — a session that died for good (revoked, deactivated, banned, or frozen), or a
  deletion the operator made as dead. An old deletion without such evidence is a
  write-off, never a death (Q11, revised: a spam limit alone is not evidence);
* censoring — a write-off: the life leaves the count without dying. A re-warm does NOT
  censor: the post-release windows run on through a later wave and count what happened
  in it, since censoring the accounts the operator chose to re-warm would bias the
  count towards the ones left alone;
* wound — a peer flood that quarantined the account, or @SpamBot newly answering
  ``limited``: the account is alive, the wound is counted beside it;
* alive needs evidence — an answer from @SpamBot (``clean`` or ``limited``, both prove
  the account is there) at or after the window's end, where ``grace`` lets in an answer
  up to one cache lifetime early, since the probe at the window's end reuses a fresh
  cached answer instead of asking again.
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
    from schemas.survival import DeathCause, LifeStatus, SurvivalOutcome, SurvivalWindow
    from services.survival._stints import Life

_RELEASE_KINDS = frozenset({"warmed", "promoted"})
_ANSWERS = frozenset({"clean", "limited"})


@dataclass(frozen=True)
class LifeFacts:
    """The moments that decide a life's outcomes."""

    released_at: datetime | None
    died_at: datetime | None
    cause: DeathCause | None
    # Written off by the operator.
    censored_at: datetime | None
    wounds: tuple[datetime, ...]
    # Moments of every answer that proves the account alive, and of any answer at all.
    alive_checks: tuple[datetime, ...]
    checks: tuple[datetime, ...]


def _verdict(event: LifecycleEvent) -> object:
    return event.extra.get("status") if event.kind == "spambot_verdict" else None


def _session_death(life: tuple[LifecycleEvent, ...]) -> bool:
    return any(event.kind == "session_dead" for event in life)


def _deletion_is_death(event: LifecycleEvent, life: tuple[LifecycleEvent, ...]) -> bool:
    """A deletion counts as a death only with evidence the account was really gone."""
    reason = event.extra.get("reason")
    if reason == "retired":
        return False
    if reason == "dead" and event.extra.get("reason_source") != "guess":
        return True  # the operator said so
    # A guess (or no reason at all): dead only on a session that died, never on a spam
    # limit — older guesses called a limited account dead, which it is not (Q11).
    return event.extra.get("evidence") == "session" or _session_death(life)


def _ending(life: Life) -> tuple[LifecycleEvent | None, DeathCause | None]:
    """The first row that ends the life and, when it is a death, its cause."""
    for event in life.events:
        if event.kind == "session_dead":
            frozen = event.extra.get("status") == "frozen"
            return event, "account_frozen" if frozen else "session_dead"
        if event.kind == "deleted":
            dead = _deletion_is_death(event, life.all_events)
            return event, "deleted_dead" if dead else None
    return None, None


def _moments(events: list[LifecycleEvent]) -> tuple[datetime, ...]:
    moments = (parse_moment(event.created_at) for event in events)
    return tuple(moment for moment in moments if moment is not None)


def _wound_rows(events: list[LifecycleEvent]) -> list[LifecycleEvent]:
    """Peer-flood quarantines, and each ``limited`` answer that follows a non-limited one."""
    wounds: list[LifecycleEvent] = []
    previous: object = None
    for event in events:
        if event.kind == "quarantined":
            wounds.append(event)
        status = _verdict(event)
        if status is None:
            continue
        if status == "limited" and previous != "limited":
            wounds.append(event)
        previous = status
    return wounds


def life_facts(life: Life) -> LifeFacts:
    """Release, death or write-off, wounds and @SpamBot answers of one life."""
    ending, cause = _ending(life)
    ended_at = parse_moment(ending.created_at) if ending is not None else None
    # Rows after the life ended say nothing about it.
    events = list(life.events)
    if ending is not None:
        events = events[: events.index(ending) + 1]
    releases = _moments([event for event in events if event.kind in _RELEASE_KINDS])
    verdicts = [event for event in events if event.kind == "spambot_verdict"]
    died_at = ended_at if cause is not None else None
    return LifeFacts(
        released_at=min(releases) if releases else None,
        died_at=died_at,
        cause=cause,
        censored_at=ended_at if ending is not None and cause is None else None,
        wounds=_moments(_wound_rows(events)),
        alive_checks=_moments([row for row in verdicts if _verdict(row) in _ANSWERS]),
        checks=_moments(verdicts),
    )


def window_end(facts: LifeFacts, window: SurvivalWindow) -> datetime | None:
    """When a post-release window closes; ``None`` before the release or for ``warming``."""
    if window == "warming" or facts.released_at is None:
        return None
    return facts.released_at + timedelta(days=SURVIVAL_WINDOW_DAYS[window])


def died_in_warming(facts: LifeFacts) -> bool:
    """The death came before (or with) the release, or there was no release at all."""
    if facts.died_at is None:
        return False
    return facts.released_at is None or facts.died_at <= facts.released_at


def _warming_outcome(facts: LifeFacts) -> SurvivalOutcome:
    if died_in_warming(facts):
        return "dead"
    if facts.released_at is not None:
        return "alive"
    return "censored" if facts.censored_at is not None else "pending"


def _closing_outcome(
    facts: LifeFacts,
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
    return "alive" if any(moment >= end - grace for moment in facts.alive_checks) else "unchecked"


def window_outcome(
    facts: LifeFacts,
    window: SurvivalWindow,
    now: datetime,
    grace: timedelta,
) -> SurvivalOutcome | None:
    """The life's outcome in ``window``; ``None`` when the life was never released.

    Only released lives enter the 7- and 30-day windows: a death during warming is
    counted once, in the warming window.
    """
    if window == "warming":
        return _warming_outcome(facts)
    end = window_end(facts, window)
    if end is None or died_in_warming(facts):
        return None
    return _closing_outcome(facts, end, now, grace)


def wounded_by(facts: LifeFacts, moment: datetime | None) -> bool:
    """A wound before ``moment`` (``None``: at any time)."""
    return any(moment is None or wound <= moment for wound in facts.wounds)


def life_status(facts: LifeFacts, grace: timedelta) -> LifeStatus:
    """Where the life stands now — the card badge and the swimmer row's colour."""
    if facts.died_at is not None:
        return "dead"
    if facts.censored_at is not None:
        return "censored"
    if facts.released_at is None:
        return "warming"
    if not any(moment >= facts.released_at - grace for moment in facts.alive_checks):
        return "unchecked"
    return "wounded" if facts.wounds else "alive"
