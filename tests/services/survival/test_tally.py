"""Counters, cohorts, causes and swimmer rows over a synthetic fleet — one count per life."""

from __future__ import annotations

from typing import TYPE_CHECKING

from services.survival._stints import build_lives
from services.survival._tally import (
    Clock,
    StintCohorts,
    causes,
    cohorts,
    deciding_wave,
    judge,
    swimmer_row,
    tally_windows,
)
from tests.services.survival._support import GRACE, at, clean, limited, released_life, row

if TYPE_CHECKING:
    from schemas.lifecycle import LifecycleEvent
    from services.survival._tally import Judged

_CLOCK = Clock(now=at(60), grace=GRACE)


def _judged(events: list[LifecycleEvent], cohort: StintCohorts | None = None) -> list[Judged]:
    fields = cohort or StintCohorts()
    return [judge(life, fields, life.account_id) for life in build_lives(events)]


def _fleet() -> list[Judged]:
    alive = [
        *released_life(account="alive"),
        clean(22, account="alive"),
        clean(45, account="alive"),
    ]
    wounded = [
        *released_life(account="wounded"),
        row("quarantined", 16, account="wounded"),
        clean(22, account="wounded"),
    ]
    dead = [
        *released_life(account="dead"),
        row("session_dead", 18, account="dead", status="unauthorized"),
    ]
    unchecked = released_life(account="unchecked")
    warming = [row("warming_started", 50, account="warming", stint=50)]
    died_warming = [
        row("warming_started", 0, account="dw"),
        row("session_dead", 4, account="dw", status="unauthorized"),
    ]
    written_off = [
        *released_life(account="off"),
        row("deleted", 16, account="off", reason="retired"),
    ]
    return _judged([*alive, *wounded, *dead, *unchecked, *warming, *died_warming, *written_off])


def test_each_window_counts_its_outcomes() -> None:
    warming, d7, d30 = tally_windows(_fleet(), _CLOCK)

    assert (warming.alive, warming.wounded, warming.dead, warming.pending) == (5, 0, 1, 1)
    assert (d7.alive, d7.wounded, d7.dead, d7.unchecked, d7.censored) == (2, 1, 1, 1, 1)
    # Day 30: only "alive" has an answer after day 44; "wounded" has none since.
    assert (d30.alive, d30.dead, d30.unchecked, d30.censored) == (1, 1, 2, 1)


def test_a_re_warmed_account_is_counted_once_in_every_window() -> None:
    events = [
        *released_life(14, account="twice"),
        row("warming_started", 18, account="twice", stint=18, wave=2),
        clean(22, account="twice", stint=18),
        row("warmed", 30, account="twice", stint=18),
        clean(45, account="twice", stint=18),
    ]
    judged = _judged(events)

    warming, d7, d30 = tally_windows(judged, _CLOCK)

    assert len(judged) == 1
    assert (warming.alive, d7.alive, d30.alive) == (1, 1, 1)
    assert sum(counter.alive + counter.dead + counter.censored for counter in (d7, d30)) == 2


def test_a_spam_block_is_counted_as_a_wound_not_a_cause_of_death() -> None:
    events = [
        *released_life(account="a"),
        limited(16, account="a"),
        clean(22, account="a"),
        *released_life(account="b"),
        row("session_dead", 17, account="b", status="frozen"),
        row("warming_started", 0, account="c"),
        row("session_dead", 3, account="c"),
    ]
    judged = _judged(events)

    assert [(cause.cause, cause.count) for cause in causes(judged)] == [
        ("account_frozen", 1),
        ("session_dead", 1),
    ]
    _warming, d7, _d30 = tally_windows(judged, _CLOCK)
    assert (d7.alive, d7.wounded) == (1, 1)


def test_week_cohorts_are_iso_weeks_of_the_deciding_wave() -> None:
    events = [
        *released_life(account="a"),
        *released_life(account="b", start=7),
        # Stopped in week 37, re-warmed and released in week 38.
        row("warming_started", 0, account="c"),
        row("warming_stopped", 1, account="c", reason="operator"),
        row("warming_started", 8, account="c", stint=8, wave=2),
        row("warmed", 20, account="c", stint=8),
    ]

    rows = cohorts(_judged(events), "week", _CLOCK)

    assert [(cohort.key, cohort.windows[0].alive) for cohort in rows] == [
        ("2026-W37", 1),
        ("2026-W38", 2),
    ]
    assert [counter.window for counter in rows[0].windows] == ["warming", "d7", "d30"]


def test_wave_cohorts_group_lives_by_their_number_of_waves() -> None:
    events = [
        *released_life(account="once"),
        *released_life(account="twice"),
        row("warming_started", 18, account="twice", stint=18, wave=2),
    ]

    rows = cohorts(_judged(events), "waves", _CLOCK)

    assert [cohort.key for cohort in rows] == ["1", "2"]


def test_the_deciding_wave_is_the_released_one_else_the_latest() -> None:
    released_first = [
        *released_life(14),
        row("warming_started", 18, stint=18, wave=2),
    ]
    released_second = [
        row("warming_started", 0),
        row("warming_started", 10, stint=10, wave=2),
        row("warmed", 24, stint=10),
        row("warming_started", 30, stint=30, wave=3),
    ]
    never = [row("warming_started", 0), row("warming_started", 10, stint=10, wave=2)]

    picks = [
        deciding_wave(build_lives(events)[0]).wave
        for events in (released_first, released_second, never)
    ]

    assert picks == [1, 2, 2]


def test_length_cohorts_sort_by_number_and_unknown_goes_last() -> None:
    judged = [
        *_judged(released_life(account="a"), StintCohorts(target_days=14)),
        *_judged(released_life(account="b"), StintCohorts(target_days=7)),
        *_judged(released_life(account="c")),
    ]

    assert [cohort.key for cohort in cohorts(judged, "length", _CLOCK)] == ["7", "14", None]


def test_persona_and_country_cohorts_read_the_stint_fields() -> None:
    judged = _judged(released_life(), StintCohorts(persona="calm", proxy_country="DE"))

    assert cohorts(judged, "persona", _CLOCK)[0].key == "calm"
    assert cohorts(judged, "proxy_country", _CLOCK)[0].key == "DE"


def test_a_swimmer_row_places_release_wounds_and_death_in_days() -> None:
    events = [
        *released_life(14),
        row("quarantined", 16.5),
        limited(17.4),
        row("session_dead", 19.2, status="account_error"),
    ]
    [item] = _judged(events)

    life = swimmer_row(item, _CLOCK)

    assert (life.release_day, life.death_day, life.end_day) == (14, 19, 19)
    assert life.wound_days == [16, 17]
    assert life.status == "dead"
    assert life.cause == "session_dead"


def test_a_swimmer_row_shows_every_wave_on_one_bar() -> None:
    events = [
        *released_life(14),
        row("warming_started", 18, stint=18, wave=2),
        row("promoted", 29.5, stint=18),
    ]
    [item] = _judged(events)

    life = swimmer_row(item, _CLOCK)

    assert [(wave.wave, wave.start_day, wave.release_day) for wave in life.waves] == [
        (1, 0, 14),
        (2, 18, 29),
    ]
    # The life's release is the first one: every window counts from it.
    assert life.release_day == 14


def test_a_living_row_runs_to_now() -> None:
    [item] = _judged(released_life(14))

    life = swimmer_row(item, _CLOCK)

    assert (life.end_day, life.death_day, life.status) == (60, None, "unchecked")
