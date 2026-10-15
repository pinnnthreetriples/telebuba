"""Life outcomes per window: alive needs an answer, a limit wounds, write-offs censor, waves."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from services.survival._outcomes import life_facts, life_status, window_outcome
from services.survival._stints import build_lives
from tests.services.survival._support import GRACE, at, clean, limited, released_life, row

if TYPE_CHECKING:
    from schemas.lifecycle import LifecycleEvent, LifecycleEventKind
    from schemas.survival import SurvivalOutcome
    from services.survival._outcomes import LifeFacts


def _facts(events: list[LifecycleEvent]) -> LifeFacts:
    return life_facts(build_lives(events)[-1])


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


def test_a_spam_limit_is_a_wound_and_its_answer_proves_the_account_alive() -> None:
    events = [*released_life(14), limited(17), limited(21.5), limited(44)]
    facts = _facts(events)

    assert facts.died_at is None
    assert facts.cause is None
    # One wound for the block, not one per scheduled answer that repeats it.
    assert facts.wounds == (at(17),)
    assert _outcomes(events, 60) == ["alive", "alive", "alive"]
    assert life_status(facts, GRACE) == "wounded"


def test_a_lifted_then_renewed_limit_is_a_second_wound() -> None:
    facts = _facts([*released_life(14), limited(16), clean(18), limited(20)])

    assert facts.wounds == (at(16), at(20))


def test_a_death_during_warming_stays_out_of_the_release_windows() -> None:
    events = [row("warming_started", 0), row("session_dead", 5, status="unauthorized")]

    assert _outcomes(events, 60) == ["dead", None, None]
    assert _facts(events).cause == "session_dead"


def test_a_frozen_account_is_dead_of_its_own_cause() -> None:
    events = [*released_life(14), clean(21), row("session_dead", 25, status="frozen")]

    assert _facts(events).cause == "account_frozen"
    assert _outcomes(events, 60) == ["alive", "alive", "dead"]


def test_a_written_off_account_is_censored_not_dead() -> None:
    events = [*released_life(14), row("deleted", 18, reason="retired")]
    facts = _facts(events)

    assert facts.died_at is None
    assert facts.censored_at == at(18)
    assert _outcomes(events, 60) == ["alive", "censored", "censored"]
    assert life_status(facts, GRACE) == "censored"


def test_deleting_a_spam_blocked_account_is_a_write_off() -> None:
    events = [*released_life(14), limited(15), row("deleted", 18, reason="retired")]

    assert _outcomes(events, 60) == ["alive", "censored", "censored"]


@pytest.mark.parametrize(
    ("kind", "extra"),
    [
        ("spambot_verdict", {"status": "limited"}),
        ("warming_stopped", {"reason": "spam_blocked"}),
    ],
)
def test_deleting_a_spam_blocked_account_as_dead_is_still_a_write_off(
    kind: LifecycleEventKind, extra: dict[str, object]
) -> None:
    events = [*released_life(14), clean(15)]
    events.append(row(kind, 16).model_copy(update={"extra": extra}))
    dead = {"reason": "dead", "reason_source": "operator"}
    events.append(row("deleted", 18).model_copy(update={"extra": dead}))

    assert _outcomes(events, 60) == ["alive", "censored", "censored"]


def test_an_operator_deletion_as_dead_is_a_death() -> None:
    events = [
        *released_life(14),
        clean(22),
        row("deleted", 25, reason="dead", reason_source="operator"),
    ]

    assert _facts(events).cause == "deleted_dead"
    assert _outcomes(events, 60) == ["alive", "alive", "dead"]


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        # Old guesses called a limited account dead: without other evidence, a write-off.
        ({"reason": "dead", "reason_source": "guess"}, "censored"),
        ({}, "censored"),
        # A guess made on a dead session names it.
        ({"reason": "dead", "reason_source": "guess", "evidence": "session"}, "dead"),
    ],
)
def test_a_guessed_or_reasonless_deletion_is_dead_only_on_evidence(
    extra: dict[str, object], expected: SurvivalOutcome
) -> None:
    # Built in journal order: the ids decide the order the rows are read in.
    events = [*released_life(14), limited(16), clean(22)]
    events.append(row("deleted", 25).model_copy(update={"extra": extra}))

    assert _outcomes(events, 60)[2] == expected


def test_a_session_death_in_the_life_backs_a_guessed_deletion() -> None:
    # The death row came first and already ends the life; the guess agrees with it.
    events = [
        *released_life(14),
        row("session_dead", 20, status="unauthorized"),
        row("deleted", 25, reason="dead", reason_source="guess"),
    ]

    assert _facts(events).cause == "session_dead"


def test_a_peer_flood_is_a_wound_not_a_death() -> None:
    events = [*released_life(14), row("quarantined", 16, status="peer_flood"), clean(22)]
    facts = _facts(events)

    assert facts.died_at is None
    assert facts.wounds == (at(16),)
    assert _outcomes(events, 25)[1] == "alive"
    assert life_status(facts, GRACE) == "wounded"


def test_a_life_without_an_end_is_still_warming() -> None:
    events = [row("warming_started", 0), clean(3)]
    facts = _facts(events)

    assert _outcomes(events, 10) == ["pending", None, None]
    assert life_status(facts, GRACE) == "warming"


def test_a_re_warm_does_not_censor_the_windows_they_run_on_through_it() -> None:
    # Released on day 14, «Вернуть в прогрев» on day 18, alive (answered) on day 22.
    events = [
        *released_life(14),
        row("warming_started", 18, stint=18, new_stint=True, wave=2),
        clean(21.5, stint=18),
    ]
    facts = _facts(events)

    assert facts.censored_at is None
    assert facts.released_at == at(14)
    assert _outcomes(events, 25) == ["alive", "alive", "pending"]


def test_a_death_in_a_later_wave_counts_in_the_windows_of_the_first_release() -> None:
    events = [
        *released_life(14),
        row("warming_started", 18, stint=18, new_stint=True, wave=2),
        clean(21.5, stint=18),
        row("session_dead", 30, stint=18, status="account_error"),
    ]

    assert _outcomes(events, 60) == ["alive", "alive", "dead"]


def test_a_life_released_only_in_its_second_wave_is_released_then() -> None:
    events = [
        row("warming_started", 0),
        row("warming_stopped", 3, reason="operator", worked_days=3),
        row("warming_started", 10, stint=10, wave=2),
        row("warmed", 24, stint=10),
    ]
    facts = _facts(events)

    assert facts.released_at == at(24)
    assert _outcomes(events, 26) == ["alive", "pending", "pending"]


def test_release_status_needs_an_answer_since_release() -> None:
    before = _facts([clean(5), *released_life(14)])
    after = _facts([*released_life(14), clean(15)])

    assert life_status(before, GRACE) == "unchecked"
    assert life_status(after, GRACE) == "alive"


def test_the_operators_promotion_releases_as_well() -> None:
    events = [row("warming_started", 0), row("promoted", 9)]

    assert _facts(events).released_at == at(9)


def test_rows_after_the_ending_say_nothing() -> None:
    events = [
        *released_life(14),
        row("session_dead", 16, status="unauthorized"),
        clean(40),
        row("quarantined", 41),
    ]
    facts = _facts(events)

    assert facts.alive_checks == ()
    assert facts.wounds == ()
