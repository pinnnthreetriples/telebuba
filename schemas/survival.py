"""Survival counter contracts: how many warmed accounts are alive after 7 and 30 days.

Read from the lifecycle journal (``schemas.lifecycle``). No behaviour, no I/O.

The unit is the account LIFE (an id between two deletions), counted once however many
warming waves it had. Its *release* is its first graduation (target reached or the
operator's promotion), in whichever wave that came. Each life is judged in three
windows: during warming (first start → release), and 7 and 30 days after release. A
later wave («Вернуть в прогрев») does not restart or end those windows: they run on
through it and count what happened in it.

Outcomes in a window:
* ``alive``     — no death by the window's end and an answer from @SpamBot (``clean`` or
                  ``limited``) at or after it;
* ``dead``      — the session died for good (revoked, deactivated, banned, frozen), or the
                  operator deleted the account as dead. A guessed or reasonless deletion is
                  a death only with that session evidence, otherwise a write-off;
* ``unchecked`` — the window has passed but no @SpamBot answer says it is alive;
* ``censored``  — written off by the operator first (a spam-blocked account deleted
                  included); out of the calculation, not a death;
* ``pending``   — the window has not passed yet.
A *wound* leaves the account alive and is counted beside it: a peer flood that put it
in quarantine, or a spam block (@SpamBot's ``limited``: no messages to strangers).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SurvivalWindow = Literal["warming", "d7", "d30"]
SURVIVAL_WINDOWS: tuple[SurvivalWindow, ...] = ("warming", "d7", "d30")
# Days after release each post-release window ends at — the product's definition.
SURVIVAL_WINDOW_DAYS: dict[SurvivalWindow, int] = {"d7": 7, "d30": 30}

SurvivalOutcome = Literal["alive", "dead", "unchecked", "censored", "pending"]
DeathCause = Literal["session_dead", "account_frozen", "deleted_dead"]
# ``waves`` groups lives by how many warming waves they have had so far.
SurvivalCohortKind = Literal["week", "persona", "length", "proxy_country", "waves"]
# Where a life stands right now: never released yet, released and alive (an answer
# since release), alive with a wound behind it, released without an answer since,
# dead, or out of the count (written off).
LifeStatus = Literal["warming", "alive", "wounded", "unchecked", "dead", "censored"]


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
    """Lives of one window by outcome; ``wounded`` is the part of ``alive`` with a wound."""

    window: SurvivalWindow
    alive: int = Field(default=0, ge=0)
    wounded: int = Field(default=0, ge=0)
    dead: int = Field(default=0, ge=0)
    unchecked: int = Field(default=0, ge=0)
    censored: int = Field(default=0, ge=0)
    pending: int = Field(default=0, ge=0)


class SurvivalCohort(BaseModel):
    """One cohort row: its key (ISO week ``2026-W37``, persona, days, country, waves).

    ``key`` is ``None`` for lives the journal cannot place (e.g. no proxy country).
    """

    key: str | None = None
    windows: list[SurvivalCounter] = Field(default_factory=list)


class SurvivalCause(BaseModel):
    """How many lives died of one cause."""

    cause: DeathCause
    count: int = Field(ge=0)


class SurvivalWave(BaseModel):
    """One wave on a life's row: its number, the day it began and its release day."""

    wave: int = Field(ge=1)
    start_day: int = Field(ge=0)
    release_day: int | None = Field(default=None, ge=0)


class SurvivalLife(BaseModel):
    """One swimmer-plot row; every ``*_day`` counts whole days from the life's first start."""

    account_id: str = Field(min_length=1)
    # The live account's label, or the id once the account is deleted.
    label: str = Field(min_length=1)
    started_at: str
    status: LifeStatus
    # Days the bar covers: to the death, the write-off, or now.
    end_day: int = Field(ge=0)
    # The life's first release, which every window counts from.
    release_day: int | None = Field(default=None, ge=0)
    death_day: int | None = Field(default=None, ge=0)
    cause: DeathCause | None = None
    wound_days: list[int] = Field(default_factory=list)
    # Every wave of the life, oldest first; a second one marks a re-warm on the bar.
    waves: list[SurvivalWave] = Field(default_factory=list)


class SurvivalReport(BaseModel):
    """The survival panel: totals, one cohort cut, causes, lives and fleet history."""

    cohort: SurvivalCohortKind
    windows: list[SurvivalCounter] = Field(default_factory=list)
    cohorts: list[SurvivalCohort] = Field(default_factory=list)
    causes: list[SurvivalCause] = Field(default_factory=list)
    lives: list[SurvivalLife] = Field(default_factory=list)
    # Released lives so far: the threshold for showing a smoothed curve.
    releases: int = Field(default=0, ge=0)
    # The earliest day the journal knows (``YYYY-MM-DD``); before it nothing is counted.
    full_data_since: str | None = None
    # Fleet-wide warming totals per day, oldest first (the daily history of 1.2).
    daily: list[FleetDailyStats] = Field(default_factory=list)


class AccountSurvival(BaseModel):
    """The card badge «День 12 после выпуска · жив» for a released account."""

    days_since_release: int = Field(ge=0)
    status: LifeStatus
    cause: DeathCause | None = None
    wounds: int = Field(default=0, ge=0)
