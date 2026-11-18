"""The probe budget as a pure decision: every rule, and the order they are checked in."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from core._config_spam_intel import SpamIntelSettings
from schemas.spam_intel import BudgetSnapshot, ProbeState, Purpose
from services.spam_intel._policy import (
    Decision,
    ProbeRequest,
    backoff_until,
    decide,
    decide_history,
    parse_moment,
)

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
CFG = SpamIntelSettings()


def _iso(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat()


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


def _snapshot(**fields: Any) -> BudgetSnapshot:
    return BudgetSnapshot.model_validate(fields)


def test_an_idle_fleet_lets_every_purpose_through() -> None:
    for purpose in ("routine", "decision", "measurement", "operator"):
        assert decide(_snapshot(), _request(purpose), CFG) == Decision(action="probe")


@pytest.mark.parametrize("purpose", ["routine", "decision", "measurement", "operator"])
def test_a_dead_session_is_refused_for_everyone(purpose: Purpose) -> None:
    decision = decide(_snapshot(), _request(purpose, alive=False), CFG)
    assert (decision.action, decision.reason) == ("refuse", "account_not_alive")


@pytest.mark.parametrize("purpose", ["routine", "decision", "measurement", "operator"])
def test_no_proxy_is_refused_for_everyone(purpose: Purpose) -> None:
    decision = decide(_snapshot(), _request(purpose, has_proxy=False), CFG)
    assert (decision.action, decision.reason) == ("refuse", "no_proxy")


def test_a_dead_session_outranks_a_missing_proxy() -> None:
    decision = decide(_snapshot(), _request(alive=False, has_proxy=False), CFG)
    assert decision.reason == "account_not_alive"


@pytest.mark.parametrize("purpose", ["routine", "decision", "measurement", "operator"])
def test_a_probe_inside_the_window_is_coalesced_for_everyone(purpose: Purpose) -> None:
    snapshot = _snapshot(last_attempt_started_at=_iso(10))
    assert decide(snapshot, _request(purpose), CFG).action == "coalesce"


def test_the_window_ends_at_the_configured_minutes() -> None:
    inside = _snapshot(last_attempt_started_at=_iso(CFG.coalesce_minutes - 1))
    outside = _snapshot(last_attempt_started_at=_iso(CFG.coalesce_minutes + 1))
    assert decide(inside, _request("decision"), CFG).action == "coalesce"
    assert decide(outside, _request("decision"), CFG).action == "probe"


def test_coalescing_outranks_the_caps_and_the_spacing() -> None:
    snapshot = _snapshot(
        last_attempt_started_at=_iso(5),
        last_fleet_started_at=_iso(1),
        fleet_count_24h=99,
    )
    assert decide(snapshot, _request(), CFG).action == "coalesce"


def test_backoff_postpones_routine_decision_and_measurement() -> None:
    until = NOW + timedelta(hours=3)
    state = ProbeState(account_id="a", fail_streak=1, next_allowed_at=until.isoformat())
    for purpose in ("routine", "decision", "measurement"):
        decision = decide(_snapshot(state=state), _request(purpose), CFG)
        assert (decision.action, decision.reason, decision.retry_at) == ("defer", "backoff", until)


def test_the_operator_is_not_held_by_backoff() -> None:
    until = NOW + timedelta(hours=3)
    state = ProbeState(account_id="a", fail_streak=1, next_allowed_at=until.isoformat())
    assert decide(_snapshot(state=state), _request("operator"), CFG).action == "probe"


def test_an_elapsed_backoff_lets_the_account_through() -> None:
    state = ProbeState(
        account_id="a", fail_streak=1, next_allowed_at=(NOW - timedelta(minutes=1)).isoformat()
    )
    assert decide(_snapshot(state=state), _request(), CFG).action == "probe"


@pytest.mark.parametrize(
    ("streak", "hours"),
    [(0, 6), (1, 6), (2, 12), (3, 24), (4, 48), (5, 48), (20, 48)],
)
def test_backoff_doubles_and_stops_at_the_cap(streak: int, hours: int) -> None:
    assert backoff_until(streak, NOW, CFG) == NOW + timedelta(hours=hours)


def test_the_account_daily_cap_defers_until_the_oldest_attempt_leaves_the_window() -> None:
    oldest = NOW - timedelta(hours=20)
    snapshot = _snapshot(
        account_count_24h=CFG.account_daily_cap,
        account_oldest_24h=oldest.isoformat(),
        last_attempt_started_at=_iso(60 * 5),
    )
    decision = decide(snapshot, _request("decision"), CFG)
    assert (decision.action, decision.reason) == ("defer", "account_daily_cap")
    assert decision.retry_at == oldest + timedelta(hours=24)


def test_the_operator_has_its_own_daily_cap_not_the_account_one() -> None:
    snapshot = _snapshot(
        account_count_24h=CFG.account_daily_cap,
        operator_count_24h=CFG.operator_daily_cap - 1,
        last_attempt_started_at=_iso(60 * 30),
    )
    assert decide(snapshot, _request("operator", confirmed=True), CFG).action == "probe"
    spent = _snapshot(operator_count_24h=CFG.operator_daily_cap)
    decision = decide(spent, _request("operator", confirmed=True), CFG)
    assert (decision.action, decision.reason) == ("refuse", "operator_daily_cap")


def test_an_out_of_turn_operator_click_needs_confirmation() -> None:
    snapshot = _snapshot(last_attempt_started_at=_iso(60 * 3))
    refused = decide(snapshot, _request("operator"), CFG)
    assert (refused.action, refused.reason) == ("refuse", "needs_confirmation")
    assert decide(snapshot, _request("operator", confirmed=True), CFG).action == "probe"


def test_an_operator_click_after_the_out_of_turn_span_needs_no_confirmation() -> None:
    old = _snapshot(last_attempt_started_at=_iso(60 * 25))
    assert decide(old, _request("operator"), CFG).action == "probe"


def test_the_hard_cap_stops_everyone_including_the_operator() -> None:
    oldest = NOW - timedelta(hours=2)
    snapshot = _snapshot(fleet_count_24h=CFG.fleet_hard_cap, fleet_oldest_24h=oldest.isoformat())
    for purpose in ("routine", "decision", "measurement", "operator"):
        decision = decide(snapshot, _request(purpose, confirmed=True), CFG)
        assert (decision.action, decision.reason) == ("defer", "fleet_hard_cap")
        assert decision.retry_at == oldest + timedelta(hours=24)


def test_the_soft_cap_waits_routine_and_measurement_but_warns_decision_and_operator() -> None:
    snapshot = _snapshot(
        fleet_count_24h=CFG.fleet_soft_cap, fleet_oldest_24h=_iso(60), last_fleet_started_at=None
    )
    for purpose in ("routine", "measurement"):
        decision = decide(snapshot, _request(purpose), CFG)
        assert (decision.action, decision.reason) == ("defer", "fleet_soft_cap")
    for purpose in ("decision", "operator"):
        decision = decide(snapshot, _request(purpose, confirmed=True), CFG)
        assert (decision.action, decision.warning) == ("probe", "soft_cap")


def test_one_below_the_soft_cap_is_not_a_warning() -> None:
    snapshot = _snapshot(fleet_count_24h=CFG.fleet_soft_cap - 1)
    assert decide(snapshot, _request("decision"), CFG) == Decision(action="probe")


def test_proxy_spacing_defers_until_the_proxy_is_free() -> None:
    started = NOW - timedelta(minutes=30)
    snapshot = _snapshot(last_proxy_started_at=started.isoformat())
    decision = decide(snapshot, _request(), CFG)
    assert (decision.action, decision.reason) == ("defer", "proxy_spacing")
    assert decision.retry_at == started + timedelta(minutes=CFG.proxy_spacing_minutes)


def test_fleet_spacing_uses_the_gap_the_caller_drew() -> None:
    started = NOW - timedelta(minutes=2)
    snapshot = _snapshot(last_fleet_started_at=started.isoformat())
    decision = decide(snapshot, _request(fleet_gap=timedelta(minutes=7)), CFG)
    assert (decision.action, decision.reason) == ("defer", "fleet_spacing")
    assert decision.retry_at == started + timedelta(minutes=7)
    assert decide(snapshot, _request(fleet_gap=timedelta(minutes=1)), CFG).action == "probe"


def test_the_operator_is_exempt_from_spacing_and_busy() -> None:
    snapshot = _snapshot(
        last_proxy_started_at=_iso(1), last_fleet_started_at=_iso(1), last_attempt_started_at=None
    )
    assert decide(snapshot, _request("operator", busy=True), CFG).action == "probe"


def test_a_busy_account_waits_for_everyone_but_its_own_feature() -> None:
    decision = decide(_snapshot(), _request("decision", busy=True), CFG)
    assert (decision.action, decision.reason) == ("defer", "busy")
    assert decision.retry_at == NOW + timedelta(minutes=CFG.busy_retry_minutes)
    assert decide(_snapshot(), _request("decision", busy=False), CFG).action == "probe"


def test_only_routine_waits_out_a_fresh_start() -> None:
    young = timedelta(minutes=2)
    routine = decide(_snapshot(), _request("routine", process_age=young), CFG)
    assert (routine.action, routine.reason) == ("defer", "startup_quiet")
    assert routine.retry_at == NOW + timedelta(minutes=CFG.startup_quiet_minutes) - young
    for purpose in ("decision", "measurement"):
        assert decide(_snapshot(), _request(purpose, process_age=young), CFG).action == "probe"


def test_history_reads_follow_the_same_permanent_refusals() -> None:
    assert decide_history(_snapshot(), _request(alive=False), CFG).reason == "account_not_alive"
    assert decide_history(_snapshot(), _request(has_proxy=False), CFG).reason == "no_proxy"
    assert decide_history(_snapshot(), _request(busy=True), CFG).reason == "busy"


def test_history_reads_keep_the_spacing_but_none_of_the_probe_caps() -> None:
    spaced = _snapshot(last_proxy_started_at=_iso(1))
    assert decide_history(spaced, _request(), CFG).reason == "proxy_spacing"
    capped = _snapshot(fleet_count_24h=999, account_count_24h=999, last_attempt_started_at=_iso(1))
    assert decide_history(capped, _request(), CFG) == Decision(action="probe")


def test_parse_moment_reads_naive_as_utc_and_garbage_as_none() -> None:
    assert parse_moment("2026-11-16T12:00:00") == NOW
    assert parse_moment("2026-11-16T12:00:00+00:00") == NOW
    assert parse_moment("nonsense") is None
    assert parse_moment(None) is None


def test_the_settings_refuse_an_inverted_budget() -> None:
    with pytest.raises(ValueError, match="fleet_gap_min_seconds"):
        SpamIntelSettings(fleet_gap_min_seconds=10.0, fleet_gap_max_seconds=1.0)
    with pytest.raises(ValueError, match="fleet_soft_cap"):
        SpamIntelSettings(fleet_soft_cap=30, fleet_hard_cap=24)
    with pytest.raises(ValueError, match="backoff_base_hours"):
        SpamIntelSettings(backoff_base_hours=72.0, backoff_max_hours=48.0)
