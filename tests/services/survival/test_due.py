"""Which account the day-7/day-30 probe asks next, and which it leaves alone."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from services.survival._due import ProbeCandidate, ProbePacing, pick_due_probe
from services.survival._outcomes import life_facts
from services.survival._stints import build_lives
from tests.services.survival._support import GRACE, at, clean, limited, released_life, row

if TYPE_CHECKING:
    from schemas.lifecycle import LifecycleEvent

_HOUR = timedelta(hours=1)


def _pacing(day: float) -> ProbePacing:
    return ProbePacing(now=at(day), grace=GRACE, spacing=_HOUR, retry=timedelta(hours=24))


def _candidate(events: list[LifecycleEvent], proxy: str = "p1") -> ProbeCandidate:
    life = build_lives(events)[-1]
    return ProbeCandidate(life.account_id, proxy, life_facts(life))


def test_day_seven_without_an_answer_is_due() -> None:
    due = pick_due_probe([_candidate(released_life(14))], {}, {}, _pacing(21.1))

    assert due is not None
    assert (due.account_id, due.window, due.window_end) == ("acc-1", "d7", at(21))


def test_before_day_seven_nothing_is_due() -> None:
    assert pick_due_probe([_candidate(released_life(14))], {}, {}, _pacing(20.9)) is None


def test_a_fresh_answer_covers_the_window() -> None:
    # Any answer since the window's end (less one cache life) — even a non-clean one.
    events = [*released_life(14), row("spambot_verdict", 20, status="unknown")]

    assert pick_due_probe([_candidate(events)], {}, {}, _pacing(22)) is None


def test_only_the_latest_closed_window_asks() -> None:
    answered_seven = [*released_life(14), clean(21.5)]

    assert pick_due_probe([_candidate(answered_seven)], {}, {}, _pacing(30)) is None
    due = pick_due_probe([_candidate(answered_seven)], {}, {}, _pacing(44.5))
    assert due is not None
    assert due.window == "d30"


def test_an_old_release_asks_once_for_day_thirty() -> None:
    due = pick_due_probe([_candidate(released_life(14))], {}, {}, _pacing(200))

    assert due is not None
    assert due.window == "d30"


def test_dead_written_off_and_unreleased_lives_are_never_asked() -> None:
    dead = _candidate(
        [*released_life(14, account="a"), row("session_dead", 15, account="a", status="frozen")]
    )
    written_off = _candidate(
        [*released_life(14, account="b"), row("deleted", 15, account="b", reason="retired")]
    )
    warming = _candidate([row("warming_started", 0, account="c")])

    assert pick_due_probe([dead, written_off, warming], {}, {}, _pacing(40)) is None


def test_a_spam_blocked_account_is_still_asked_it_is_alive() -> None:
    wounded = _candidate([*released_life(14), limited(15)])

    due = pick_due_probe([wounded], {}, {}, _pacing(21.5))

    assert due is not None
    assert due.window == "d7"


def test_the_most_overdue_goes_first() -> None:
    late = _candidate(released_life(10, account="late"), proxy="p1")
    early = _candidate(released_life(14, account="early"), proxy="p2")

    due = pick_due_probe([early, late], {}, {}, _pacing(25))

    assert due is not None
    assert due.account_id == "late"


def test_a_proxy_asked_within_the_hour_waits() -> None:
    candidate = _candidate(released_life(14))

    assert pick_due_probe([candidate], {"p1": at(22) - 0.5 * _HOUR}, {}, _pacing(22)) is None
    assert pick_due_probe([candidate], {"p1": at(22) - 2 * _HOUR}, {}, _pacing(22)) is not None
    assert pick_due_probe([candidate], {"p2": at(22)}, {}, _pacing(22)) is not None


def test_an_unanswered_probe_is_not_retried_within_a_day() -> None:
    candidate = _candidate(released_life(14))

    assert pick_due_probe([candidate], {}, {"acc-1": at(21.5)}, _pacing(22)) is None
    assert pick_due_probe([candidate], {}, {"acc-1": at(20.5)}, _pacing(22)) is not None
