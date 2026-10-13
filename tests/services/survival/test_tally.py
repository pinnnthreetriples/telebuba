"""Counters, cohorts, causes and swimmer rows over a synthetic fleet."""

from __future__ import annotations

from typing import TYPE_CHECKING

from services.survival._stints import build_stints
from services.survival._tally import (
    Clock,
    StintCohorts,
    causes,
    cohorts,
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
    return [judge(stint, fields, stint.account_id) for stint in build_stints(events)]


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
    dead = [*released_life(account="dead"), limited(18, account="dead")]
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


def test_causes_are_counted_commonest_first() -> None:
    events = [
        *released_life(account="a"),
        limited(16, account="a"),
        *released_life(account="b"),
        limited(17, account="b"),
        row("warming_started", 0, account="c"),
        row("session_dead", 3, account="c"),
    ]

    assert [(cause.cause, cause.count) for cause in causes(_judged(events))] == [
        ("spam_limited", 2),
        ("session_dead", 1),
    ]


def test_week_cohorts_are_iso_weeks_of_the_start() -> None:
    events = [*released_life(account="a"), *released_life(account="b", start=7)]

    rows = cohorts(_judged(events), "week", _CLOCK)

    assert [cohort.key for cohort in rows] == ["2026-W37", "2026-W38"]
    assert [counter.window for counter in rows[0].windows] == ["warming", "d7", "d30"]


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
        limited(19.2),
    ]
    [item] = _judged(events)

    stint = swimmer_row(item, _CLOCK)

    assert (stint.release_day, stint.death_day, stint.end_day) == (14, 19, 19)
    assert stint.wound_days == [16]
    assert stint.status == "dead"
    assert stint.cause == "spam_limited"


def test_a_living_row_runs_to_now() -> None:
    [item] = _judged(released_life(14))

    stint = swimmer_row(item, _CLOCK)

    assert (stint.end_day, stint.death_day, stint.status) == (60, None, "unchecked")
