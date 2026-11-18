"""D13 and the transient statuses as pure rules: the local night and the wait for a status.

Zones are named, never guessed. ``Etc/GMT-3`` is UTC+3 all year (no daylight saving), so a clock
reading in it means the same thing in every season; ``Europe/Berlin`` is used only to show the rule
holds on both sides of a clock change.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from core._config_spam_intel import SpamIntelSettings
from schemas.spam_intel import BudgetSnapshot, Purpose
from services.spam_intel._night import night_until
from services.spam_intel._policy import ProbeRequest, decide, decide_history

CFG = SpamIntelSettings()
FIXED = "Etc/GMT-3"  # UTC+3 all year


def _utc(hour: int, minute: int = 0, *, month: int = 11, day: int = 18) -> datetime:
    """A UTC instant; in ``FIXED`` it reads three hours later."""
    return datetime(2026, month, day, hour, minute, tzinfo=UTC)


def _cfg(**overrides: Any) -> SpamIntelSettings:
    return SpamIntelSettings.model_validate(overrides)


def test_inside_the_night_the_account_waits_for_its_local_morning() -> None:
    # 23:30 UTC is 02:30 in UTC+3: night; the morning is 07:00 local = 04:00 UTC the same day.
    until = night_until(_utc(23, 30), FIXED, "acc-1", _cfg(local_night_spread_minutes=0.0))
    assert until == _utc(4, 0, day=19)


def test_the_morning_is_staggered_by_a_stable_offset_per_account() -> None:
    moment = _utc(23, 30)
    first = night_until(moment, FIXED, "acc-1", CFG)
    again = night_until(moment, FIXED, "acc-1", CFG)
    other = night_until(moment, FIXED, "acc-2", CFG)
    assert first is not None
    assert other is not None
    assert first == again
    assert first != other
    for wake in (first, other):
        assert _utc(4, 0, day=19) <= wake < _utc(4, 0, day=19) + timedelta(minutes=90)


def test_the_stagger_holds_a_little_past_the_end_of_the_night() -> None:
    cfg = _cfg(local_night_spread_minutes=90.0)
    wake = night_until(_utc(23, 30), FIXED, "acc-1", cfg)
    assert wake is not None
    assert night_until(wake - timedelta(seconds=1), FIXED, "acc-1", cfg) == wake
    assert night_until(wake, FIXED, "acc-1", cfg) is None


def test_by_day_there_is_nothing_to_wait_for() -> None:
    assert night_until(_utc(9, 0), FIXED, "acc-1", CFG) is None  # 12:00 local
    assert night_until(_utc(20, 59), FIXED, "acc-1", CFG) is None  # 23:59 local


def test_the_night_starts_at_local_midnight() -> None:
    assert night_until(_utc(20, 59), FIXED, "acc-1", CFG) is None
    assert night_until(_utc(21, 0), FIXED, "acc-1", CFG) is not None  # 00:00 local


def test_a_night_that_wraps_midnight_is_read_across_the_day_boundary() -> None:
    cfg = _cfg(local_night_start_hour=23, local_night_end_hour=7, local_night_spread_minutes=0.0)
    assert night_until(_utc(19, 59), FIXED, "a", cfg) is None  # 22:59 local
    assert night_until(_utc(20, 30), FIXED, "a", cfg) == _utc(4, 0, day=19)  # 23:30 local
    assert night_until(_utc(2, 0), FIXED, "a", cfg) == _utc(4, 0)  # 05:00 local, next morning


@pytest.mark.parametrize("zone", [None, "", "Not/AZone", "nonsense"])
def test_an_unknown_zone_is_never_held(zone: str | None) -> None:
    assert night_until(_utc(23, 30), zone, "acc-1", CFG) is None


def test_the_switch_and_an_empty_window_turn_it_off() -> None:
    assert night_until(_utc(23, 30), FIXED, "a", _cfg(local_night_defer=False)) is None
    empty = _cfg(local_night_start_hour=3, local_night_end_hour=3)
    assert night_until(_utc(23, 30), FIXED, "a", empty) is None


@pytest.mark.parametrize("month", [1, 7])
def test_a_zone_with_daylight_saving_holds_the_same_local_night_in_either_season(
    month: int,
) -> None:
    cfg = _cfg(local_night_spread_minutes=0.0)
    # 03:00 in Berlin is 02:00 UTC in winter and 01:00 UTC in summer.
    offset_hours = 1 if month == 1 else 2
    moment = datetime(2026, month, 15, 3 - offset_hours, 0, tzinfo=UTC)
    until = night_until(moment, "Europe/Berlin", "a", cfg)
    assert until == datetime(2026, month, 15, 7 - offset_hours, 0, tzinfo=UTC)
    noon = datetime(2026, month, 15, 12 - offset_hours, 0, tzinfo=UTC)
    assert night_until(noon, "Europe/Berlin", "a", cfg) is None


def test_the_hours_and_the_spread_are_validated() -> None:
    for name in ("local_night_start_hour", "local_night_end_hour"):
        with pytest.raises(ValueError, match=name):
            SpamIntelSettings.model_validate({name: 24})
        with pytest.raises(ValueError, match=name):
            SpamIntelSettings.model_validate({name: -1})
    with pytest.raises(ValueError, match="local_night_spread_minutes"):
        SpamIntelSettings(local_night_spread_minutes=-5.0)


NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
_BASE = ProbeRequest(
    purpose="routine",
    now=NOW,
    alive=True,
    has_proxy=True,
    busy=False,
    confirmed=False,
    process_age=timedelta(hours=5),
    fleet_gap=timedelta(minutes=5),
)


def _request(purpose: Purpose = "routine", **overrides: Any) -> ProbeRequest:
    return replace(_BASE, purpose=purpose, **overrides)


def test_only_a_routine_ask_is_held_by_the_local_night() -> None:
    until = NOW + timedelta(hours=4)
    held = decide(BudgetSnapshot(), _request(night_until=until), CFG)
    assert (held.action, held.reason, held.retry_at) == ("defer", "local_night", until)
    for purpose in ("decision", "measurement", "operator"):
        assert decide(BudgetSnapshot(), _request(purpose, night_until=until), CFG).action == "probe"


def test_a_night_that_has_ended_holds_nothing() -> None:
    past = NOW - timedelta(seconds=1)
    assert decide(BudgetSnapshot(), _request(night_until=past), CFG).action == "probe"


def test_the_night_comes_after_busy_and_the_quiet_start() -> None:
    until = NOW + timedelta(hours=4)
    assert decide(BudgetSnapshot(), _request(busy=True, night_until=until), CFG).reason == "busy"
    young = _request(process_age=timedelta(minutes=1), night_until=until)
    assert decide(BudgetSnapshot(), young, CFG).reason == "startup_quiet"


@pytest.mark.parametrize("purpose", ["routine", "decision", "measurement", "operator"])
def test_a_transient_status_defers_every_purpose_with_the_configured_wait(
    purpose: Purpose,
) -> None:
    decision = decide(BudgetSnapshot(), _request(purpose, transient=True), CFG)
    assert (decision.action, decision.reason) == ("defer", "account_status_transient")
    assert decision.retry_at == NOW + timedelta(minutes=CFG.account_status_retry_minutes)


def test_a_dead_session_is_still_refused_and_outranks_a_transient_one() -> None:
    decision = decide(BudgetSnapshot(), _request(alive=False, transient=True), CFG)
    assert (decision.action, decision.reason) == ("refuse", "account_not_alive")
    no_proxy = decide(BudgetSnapshot(), _request(has_proxy=False, transient=True), CFG)
    assert no_proxy.reason == "no_proxy"


def test_a_reading_inside_the_window_is_still_handed_back_for_a_transient_account() -> None:
    recent = BudgetSnapshot(last_attempt_started_at=(NOW - timedelta(minutes=5)).isoformat())
    assert decide(recent, _request(transient=True), CFG).action == "coalesce"


def test_a_history_read_waits_for_a_transient_status_too() -> None:
    decision = decide_history(BudgetSnapshot(), _request(transient=True), CFG)
    assert (decision.action, decision.reason) == ("defer", "account_status_transient")
