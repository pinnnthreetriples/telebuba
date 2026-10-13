"""Stint outcomes per window: alive needs a check, write-offs censor, Q7 wounds, Q11 guesses."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from services.survival._outcomes import stint_facts, stint_status, window_outcome
from services.survival._stints import build_stints
from tests.services.survival._support import GRACE, at, clean, limited, released_life, row

if TYPE_CHECKING:
    from schemas.lifecycle import LifecycleEvent
    from schemas.survival import SurvivalOutcome
    from services.survival._outcomes import StintFacts


def _facts(events: list[LifecycleEvent]) -> StintFacts:
    return stint_facts(build_stints(events)[-1])


def _outcomes(events: list[LifecycleEvent], now_day: float) -> list[SurvivalOutcome | None]:
    facts = _facts(events)
    return [
        window_outcome(facts, window, at(now_day), GRACE) for window in ("warming", "d7", "d30")
    ]


def test_a_clean_check_after_day_seven_is_alive_and_day_thirty_waits() -> None:
    events = [*released_life(14), clean(21.5)]

    assert _outcomes(events, 25) == ["alive", "alive", "pending"]


def test_without_a_check_a_passed_window_is_unchecked() -> None:
    assert _outcomes(released_life(14), 50) == ["alive", "unchecked", "unchecked"]


def test_a_cached_answer_within_one_cache_life_counts_an_older_one_does_not() -> None:
    # The day-7 probe reuses a fresh cached answer: up to GRACE before the window's end.
    inside = [*released_life(14), clean(21 - 1.4)]
    outside = [*released_life(14), clean(21 - 1.6)]

    assert _outcomes(inside, 22)[1] == "alive"
    assert _outcomes(outside, 22)[1] == "unchecked"


def test_a_limit_after_release_is_a_death_in_both_windows() -> None:
    facts = _facts([*released_life(14), limited(17)])

    assert facts.cause == "spam_limited"
    assert _outcomes([*released_life(14), limited(17)], 60) == ["alive", "dead", "dead"]
    assert stint_status(facts, GRACE) == "dead"


def test_a_limit_between_day_seven_and_thirty_dies_only_in_the_thirty() -> None:
    events = [*released_life(14), clean(21), limited(30)]

    assert _outcomes(events, 60) == ["alive", "alive", "dead"]


def test_a_death_during_warming_stays_out_of_the_release_windows() -> None:
    events = [row("warming_started", 0), row("session_dead", 5, status="unauthorized")]

    assert _outcomes(events, 60) == ["dead", None, None]
    assert _facts(events).cause == "session_dead"


def test_a_written_off_account_is_censored_not_dead() -> None:
    events = [*released_life(14), row("deleted", 18, reason="retired")]
    facts = _facts(events)

    assert facts.died_at is None
    assert facts.censored_at == at(18)
    assert _outcomes(events, 60) == ["alive", "censored", "censored"]
    assert stint_status(facts, GRACE) == "censored"


def test_a_deletion_as_dead_is_a_death() -> None:
    events = [*released_life(14), clean(22), row("deleted", 25, reason="dead")]

    assert _facts(events).cause == "deleted_dead"
    assert _outcomes(events, 60) == ["alive", "alive", "dead"]


@pytest.mark.parametrize(
    ("limited_before", "expected"),
    [(True, "dead"), (False, "censored")],
)
def test_an_old_deletion_without_a_reason_is_guessed_from_the_life(
    limited_before: bool,  # noqa: FBT001 - parametrised flag
    expected: str,
) -> None:
    # Q11: the limited answer came in an earlier stint of the same life.
    first = [row("warming_started", 0), *([limited(3)] if limited_before else [])]
    second = [row("warming_started", 10, stint=10), row("warmed", 24, stint=10)]
    events = [*first, *second, row("deleted", 28, stint=10)]

    assert _outcomes(events, 60)[1] == expected


def test_a_spam_block_without_a_limit_is_a_wound_not_a_death() -> None:
    events = [*released_life(14), row("quarantined", 16, status="peer_flood"), clean(22)]
    facts = _facts(events)

    assert facts.died_at is None
    assert facts.wounds == (at(16),)
    assert _outcomes(events, 25)[1] == "alive"
    assert stint_status(facts, GRACE) == "wounded"


def test_a_stint_without_an_end_is_still_warming() -> None:
    events = [row("warming_started", 0), clean(3)]
    facts = _facts(events)

    assert _outcomes(events, 10) == ["pending", None, None]
    assert stint_status(facts, GRACE) == "warming"


def test_a_stint_warmed_again_from_scratch_is_censored_at_the_restart() -> None:
    events = [*released_life(14), row("warming_started", 18, stint=18)]
    facts = stint_facts(build_stints(events)[0])

    assert facts.censored_at == at(18)
    assert window_outcome(facts, "d7", at(60), GRACE) == "censored"


def test_a_stint_cut_before_its_release_is_censored_in_warming() -> None:
    events = [row("warming_started", 0), row("warming_started", 5, stint=5)]
    facts = stint_facts(build_stints(events)[0])

    assert window_outcome(facts, "warming", at(60), GRACE) == "censored"


def test_release_status_needs_a_clean_answer_since_release() -> None:
    before = _facts([clean(5), *released_life(14)])
    after = _facts([*released_life(14), clean(15)])

    assert stint_status(before, GRACE) == "unchecked"
    assert stint_status(after, GRACE) == "alive"


def test_the_operators_promotion_releases_as_well() -> None:
    events = [row("warming_started", 0), row("promoted", 9)]

    assert _facts(events).released_at == at(9)


def test_rows_after_the_ending_say_nothing() -> None:
    events = [*released_life(14), limited(16), clean(40), row("quarantined", 41)]
    facts = _facts(events)

    assert facts.clean_checks == ()
    assert facts.wounds == ()
