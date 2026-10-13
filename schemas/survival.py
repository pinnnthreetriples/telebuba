"""Survival counter contracts: how many warmed accounts are alive after 7 and 30 days.

Read from the lifecycle journal (``schemas.lifecycle``). No behaviour, no I/O.

A stint is one warming run of one account life; its *release* is the first graduation
(target reached or the operator's promotion). Each stint is judged in three windows:
during warming (start → release), and 7 and 30 days after release.

Outcomes in a window:
* ``alive``     — no death by the window's end and a clean @SpamBot answer at or after it;
* ``dead``      — @SpamBot said limited, the session died for good, or the operator deleted
                  the account as dead (an old deletion without a reason: dead only if
                  @SpamBot had limited that life, otherwise a write-off);
* ``unchecked`` — the window has passed but no @SpamBot answer says it is alive;
* ``censored``  — written off by the operator (or warmed again from scratch) first;
                  out of the calculation, not a death;
* ``pending``   — the window has not passed yet.
A spam block that @SpamBot did not turn into a restriction is a *wound*: the account
stays alive and the wound is counted beside it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SurvivalWindow = Literal["warming", "d7", "d30"]
SURVIVAL_WINDOWS: tuple[SurvivalWindow, ...] = ("warming", "d7", "d30")
# Days after release each post-release window ends at — the product's definition.
SURVIVAL_WINDOW_DAYS: dict[SurvivalWindow, int] = {"d7": 7, "d30": 30}

SurvivalOutcome = Literal["alive", "dead", "unchecked", "censored", "pending"]
DeathCause = Literal["spam_limited", "session_dead", "deleted_dead"]
SurvivalCohortKind = Literal["week", "persona", "length", "proxy_country"]
# Where a stint stands right now: still warming, released and alive (a clean answer
# since release), alive with a spam block behind it, released without a clean answer
# since, dead, or out of the count (written off, or warmed again from scratch).
StintStatus = Literal["warming", "alive", "wounded", "unchecked", "dead", "censored"]


class FleetDailyStats(BaseModel):
    """Every account's totals of one day added up — the fleet's daily history."""

    day: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    accounts: int = Field(default=0, ge=0)
    cycles: int = Field(default=0, ge=0)
    reads: int = Field(default=0, ge=0)
    reactions: int = Field(default=0, ge=0)
    joins: int = Field(default=0, ge=0)
    dms: int = Field(default=0, ge=0)
    failures: int = Field(default=0, ge=0)
    floods: int = Field(default=0, ge=0)


class SurvivalCounter(BaseModel):
    """Stints of one window by outcome; ``wounded`` is the part of ``alive`` with a wound."""

    window: SurvivalWindow
    alive: int = Field(default=0, ge=0)
    wounded: int = Field(default=0, ge=0)
    dead: int = Field(default=0, ge=0)
    unchecked: int = Field(default=0, ge=0)
    censored: int = Field(default=0, ge=0)
    pending: int = Field(default=0, ge=0)


class SurvivalCohort(BaseModel):
    """One cohort row: its key (ISO week ``2026-W37``, persona, days, country) and windows.

    ``key`` is ``None`` for stints the journal cannot place (e.g. no proxy country).
    """

    key: str | None = None
    windows: list[SurvivalCounter] = Field(default_factory=list)


class SurvivalCause(BaseModel):
    """How many stints died of one cause."""

    cause: DeathCause
    count: int = Field(ge=0)


class SurvivalStint(BaseModel):
    """One swimmer-plot row; every ``*_day`` counts whole days from the stint's start."""

    account_id: str = Field(min_length=1)
    # The live account's label, or the id once the account is deleted.
    label: str = Field(min_length=1)
    started_at: str
    status: StintStatus
    # Days the bar covers: to the death, the write-off, or now.
    end_day: int = Field(ge=0)
    release_day: int | None = Field(default=None, ge=0)
    death_day: int | None = Field(default=None, ge=0)
    cause: DeathCause | None = None
    wound_days: list[int] = Field(default_factory=list)


class SurvivalReport(BaseModel):
    """The survival panel: totals, one cohort cut, causes, stints and fleet history."""

    cohort: SurvivalCohortKind
    windows: list[SurvivalCounter] = Field(default_factory=list)
    cohorts: list[SurvivalCohort] = Field(default_factory=list)
    causes: list[SurvivalCause] = Field(default_factory=list)
    stints: list[SurvivalStint] = Field(default_factory=list)
    # Releases so far: the threshold for showing a smoothed curve.
    releases: int = Field(default=0, ge=0)
    # The earliest day the journal knows (``YYYY-MM-DD``); before it nothing is counted.
    full_data_since: str | None = None
    # Fleet-wide warming totals per day, oldest first (the daily history of 1.2).
    daily: list[FleetDailyStats] = Field(default_factory=list)


class AccountSurvival(BaseModel):
    """The card badge «День 12 после выпуска · жив» for a released account."""

    days_since_release: int = Field(ge=0)
    status: StintStatus
    cause: DeathCause | None = None
    wounds: int = Field(default=0, ge=0)
