"""The adaptive routine interval (D2): 72 h stays warming's, 120 h and 168 h after clean runs."""

from __future__ import annotations

from datetime import timedelta

import pytest

from core._config_spam_intel import SpamIntelSettings
from schemas.spam_evidence import Evidence, EvidenceKind  # noqa: TC001 - annotations
from services.spam_intel._adaptive import clean_streak, last_clean_at, routine_interval
from tests.services.spam_intel._evidence_support import CFG, NOW, ev


def _cleans(count: int, *, every_hours: float = 72.0, newest_ago: float = 10.0) -> list[Evidence]:
    return [ev("spambot_clean", newest_ago + i * every_hours) for i in range(count)]


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (0, None),
        (2, None),
        (3, timedelta(hours=120)),
        (4, timedelta(hours=120)),
        (5, timedelta(hours=168)),
        (9, timedelta(hours=168)),
    ],
)
def test_the_interval_grows_with_the_clean_run(count: int, expected: timedelta | None) -> None:
    assert routine_interval(_cleans(count), CFG) == expected


@pytest.mark.parametrize(
    "breaker",
    ["peer_flood_send", "peer_flood_other", "user_banned_in_channel", "spambot_limited"],
)
def test_any_limiting_evidence_inside_the_run_resets_it(breaker: EvidenceKind) -> None:
    items = [*_cleans(6, newest_ago=40.0), ev(breaker, 20.0)]
    assert clean_streak(items) == 0
    assert routine_interval(items, CFG) is None


def test_limiting_evidence_before_the_run_does_not_reset_the_newer_cleans() -> None:
    items = [ev("peer_flood_send", 400.0), *_cleans(5, newest_ago=10.0)]
    assert clean_streak(items) == 5


@pytest.mark.parametrize("silent", ["spambot_unreadable", "spambot_reviewing", "freeze_seen"])
def test_an_answer_that_said_nothing_or_a_freeze_ends_the_run(silent: EvidenceKind) -> None:
    items = [*_cleans(4, newest_ago=40.0), ev(silent, 20.0)]
    assert clean_streak(items) == 0


@pytest.mark.parametrize("neutral", ["tos_restricted", "flood_wait", "group_send_ok", "join_ok"])
def test_evidence_that_moves_nothing_does_not_end_the_run(neutral: EvidenceKind) -> None:
    items = [*_cleans(3, newest_ago=40.0), ev(neutral, 20.0)]
    assert clean_streak(items) == 3


def test_the_thresholds_and_hours_come_from_the_settings() -> None:
    cfg = SpamIntelSettings(
        routine_mid_after_clean=1,
        routine_long_after_clean=2,
        routine_mid_hours=100.0,
        routine_long_hours=200.0,
    )
    assert routine_interval(_cleans(1), cfg) == timedelta(hours=100)
    assert routine_interval(_cleans(2), cfg) == timedelta(hours=200)


def test_inverted_thresholds_are_refused_at_startup() -> None:
    with pytest.raises(ValueError, match="routine_mid_hours"):
        SpamIntelSettings(routine_mid_hours=200.0, routine_long_hours=100.0)
    with pytest.raises(ValueError, match="routine_mid_after_clean"):
        SpamIntelSettings(routine_mid_after_clean=6, routine_long_after_clean=5)


def test_the_newest_clean_reading_is_the_last_one() -> None:
    assert last_clean_at([]) is None
    assert last_clean_at(_cleans(3, newest_ago=10.0)) == NOW - timedelta(hours=10)
