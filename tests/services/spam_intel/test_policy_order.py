"""The budget's rule order is design 5.5's, and every interval the settings accept is usable.

The order decides only WHICH reason an ask is turned away with (and its ``retry_at``), never
whether it goes: any one deferral stops the probe. It is still the contract, because a caller
and an operator read that reason.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from core._config_spam_intel import SpamIntelSettings
from schemas.spam_intel import BudgetSnapshot, ProbeState, Purpose
from services.spam_intel._policy import Decision, ProbeRequest, backoff_until, decide

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
CFG = SpamIntelSettings()

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


def _iso(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat()


def _request(purpose: Purpose = "routine", **overrides: Any) -> ProbeRequest:
    return replace(_BASE, purpose=purpose, **overrides)


def _everything() -> BudgetSnapshot:
    """A snapshot that trips every rule below coalescing at once."""
    state = ProbeState(
        account_id="a", fail_streak=1, next_allowed_at=(NOW + timedelta(hours=1)).isoformat()
    )
    return BudgetSnapshot(
        state=state,
        account_count_24h=CFG.account_daily_cap,
        last_proxy_started_at=_iso(1),
        last_fleet_started_at=_iso(1),
        fleet_count_24h=CFG.fleet_hard_cap,
        fleet_oldest_24h=_iso(60),
        last_attempt_started_at=_iso(60 * 5),
    )


def test_a_routine_ask_meets_the_rules_in_the_documented_order() -> None:
    """5.5: account cap (4), backoff (6), proxy (7), fleet gap (8), caps (9), busy, quiet start."""
    young = timedelta(minutes=1)
    steps: list[tuple[dict[str, Any], ProbeRequest, str | None]] = [
        ({}, _request(busy=True, process_age=young), "account_daily_cap"),
        ({"account_count_24h": 0}, _request(busy=True, process_age=young), "backoff"),
        ({"state": None}, _request(busy=True, process_age=young), "proxy_spacing"),
        ({"last_proxy_started_at": None}, _request(busy=True, process_age=young), "fleet_spacing"),
        ({"last_fleet_started_at": None}, _request(busy=True, process_age=young), "fleet_hard_cap"),
        (
            {"fleet_count_24h": CFG.fleet_soft_cap},
            _request(busy=True, process_age=young),
            "fleet_soft_cap",
        ),
        ({"fleet_count_24h": 0}, _request(busy=True, process_age=young), "busy"),
        ({}, _request(process_age=young), "startup_quiet"),
        ({}, _request(), None),
    ]
    snapshot = _everything()
    for update, request, expected in steps:
        snapshot = snapshot.model_copy(update=update)
        assert decide(snapshot, request, CFG).reason == expected


def test_busy_outranks_the_quiet_start_and_both_come_after_the_caps() -> None:
    young = timedelta(minutes=1)
    clear = BudgetSnapshot()
    assert decide(clear, _request(busy=True, process_age=young), CFG).reason == "busy"
    assert decide(clear, _request(process_age=young), CFG).reason == "startup_quiet"
    capped = BudgetSnapshot(fleet_count_24h=CFG.fleet_hard_cap)
    request = _request(busy=True, process_age=young)
    assert decide(capped, request, CFG).reason == "fleet_hard_cap"
    soft = BudgetSnapshot(fleet_count_24h=CFG.fleet_soft_cap)
    assert decide(soft, request, CFG).reason == "fleet_soft_cap"


def test_the_hard_cap_comes_after_backoff_and_spacing_but_still_stops_everyone() -> None:
    capped = BudgetSnapshot(
        fleet_count_24h=CFG.fleet_hard_cap,
        fleet_oldest_24h=_iso(60),
        last_proxy_started_at=_iso(1),
    )
    assert decide(capped, _request("decision"), CFG).reason == "proxy_spacing"
    open_proxy = capped.model_copy(update={"last_proxy_started_at": None})
    for purpose in ("routine", "decision", "measurement", "operator"):
        decision = decide(open_proxy, _request(purpose, confirmed=True), CFG)
        assert (decision.action, decision.reason) == ("defer", "fleet_hard_cap")


@pytest.mark.parametrize("purpose", ["decision", "operator"])
def test_a_decision_at_the_soft_cap_still_meets_busy_before_it_goes(purpose: Purpose) -> None:
    soft = BudgetSnapshot(fleet_count_24h=CFG.fleet_soft_cap)
    went = decide(soft, _request(purpose, confirmed=True), CFG)
    assert (went.action, went.warning) == ("probe", "soft_cap")
    held = decide(soft, _request(purpose, confirmed=True, busy=True), CFG)
    if purpose == "decision":
        assert (held.action, held.reason) == ("defer", "busy")
    else:
        assert held.action == "probe"  # the operator's click is exempt from busy


def test_the_operator_meets_coalescing_then_the_hard_cap_then_its_own_limits() -> None:
    snapshot = BudgetSnapshot(
        last_attempt_started_at=_iso(5),
        fleet_count_24h=CFG.fleet_hard_cap,
        operator_count_24h=CFG.operator_daily_cap,
    )
    assert decide(snapshot, _request("operator"), CFG).action == "coalesce"
    snapshot = snapshot.model_copy(update={"last_attempt_started_at": _iso(60 * 3)})
    assert decide(snapshot, _request("operator"), CFG).reason == "fleet_hard_cap"
    snapshot = snapshot.model_copy(update={"fleet_count_24h": 0})
    assert decide(snapshot, _request("operator"), CFG).reason == "operator_daily_cap"
    snapshot = snapshot.model_copy(update={"operator_count_24h": 0})
    assert decide(snapshot, _request("operator"), CFG).reason == "needs_confirmation"
    assert decide(snapshot, _request("operator", confirmed=True), CFG) == Decision(action="probe")


def _float_fields() -> list[str]:
    return [
        name for name, field in SpamIntelSettings.model_fields.items() if field.annotation is float
    ]


@pytest.mark.parametrize("name", _float_fields())
@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan, -1.0, 1e300])
def test_no_interval_or_range_accepts_a_value_that_cannot_be_used(name: str, value: float) -> None:
    with pytest.raises(ValueError, match=name):
        SpamIntelSettings.model_validate({name: value})


def test_coalesce_minutes_infinity_is_refused_at_startup_not_at_the_first_ask() -> None:
    with pytest.raises(ValueError, match="coalesce_minutes"):
        SpamIntelSettings(coalesce_minutes=math.inf)


def test_the_documented_defaults_are_all_valid() -> None:
    for name in _float_fields():
        value = getattr(CFG, name)
        assert math.isfinite(value)
        assert value >= 0.0


@pytest.mark.parametrize("streak", [0, 1, 50, 5000, 10**9])
def test_backoff_never_overflows_however_long_the_failure_streak(streak: int) -> None:
    assert backoff_until(streak, NOW, CFG) <= NOW + timedelta(hours=CFG.backoff_max_hours)
