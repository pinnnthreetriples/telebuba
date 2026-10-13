"""The warming card's console rows (Прогрев 2.0, item 2.3).

Settled actions, DM status, the attention item and the geo verdict — codes only, the SPA
owns the words.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.config import settings
from schemas.warming import (
    WarmingIntensity,
    WarmingReadiness,
    WarmingSettings,
    WarmingStateRecord,
)
from services.warming._console import (
    account_geo,
    attention,
    dm_status,
    geo_accepted,
    geo_pair,
    settled_today,
)
from tests.services.warming._support import _account

_NOW = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)
_READY = WarmingReadiness(ready=True)
_NOT_READY = WarmingReadiness(ready=False, reasons=["no proxy"])


def _record(**fields: object) -> WarmingStateRecord:
    base: dict[str, object] = {"account_id": "acc-1", "state": "active", "updated_at": "t"}
    base.update(fields)
    return WarmingStateRecord.model_validate(base)


def _settings(**fields: object) -> WarmingSettings:
    base: dict[str, object] = {
        "inter_account_chat": True,
        "has_gemini_key": True,
        "enforce_readiness": True,
        "gemini_model": "gemini",
        "updated_at": "t",
    }
    base.update(fields)
    return WarmingSettings.model_validate(base)


def _intensity(*, dm_allowed: bool) -> WarmingIntensity:
    return WarmingIntensity(
        channels_min=1, channels_max=1, reaction_probability=0.5, dm_allowed=dm_allowed
    )


def test_a_cycle_in_flight_shows_what_was_settled_before_its_booking() -> None:
    running = _record(last_event="cycle_started", daily_actions=15, cycle_base_actions=6)

    assert settled_today(running) == 6


def test_a_settled_row_shows_its_own_count() -> None:
    parked = _record(state="sleeping", last_event="cycle:ok", daily_actions=9, cycle_base_actions=6)

    assert settled_today(parked) == 9
    assert settled_today(None) == 0
    # A row booked before the base column existed falls back to the stored count.
    assert settled_today(_record(last_event="cycle_started", daily_actions=4)) == 4


@pytest.mark.parametrize(
    ("controls", "readiness", "dm_allowed", "started_hours_ago", "expected"),
    [
        ({"inter_account_chat": False}, _READY, True, 100, "chat_off"),
        ({"has_gemini_key": False}, _READY, True, 100, "no_llm_key"),
        ({"dm_paused_until": "2026-06-14T00:00:00+00:00"}, _READY, True, 100, "paused"),
        ({}, _NOT_READY, True, 100, "not_ready"),
        ({"enforce_readiness": False}, _NOT_READY, True, 100, "on"),
        ({}, _READY, True, 100, "on"),
        ({}, _READY, False, 1, "too_young"),
        ({}, _READY, False, 100, "trust"),
        ({"has_gemini_key": False, "has_deepseek_key": True}, _READY, True, 100, "on"),
    ],
)
def test_dm_status_names_the_first_blocker(
    controls: dict[str, object],
    readiness: WarmingReadiness,
    *,
    dm_allowed: bool,
    started_hours_ago: int,
    expected: str,
) -> None:
    started = (_NOW - timedelta(hours=started_hours_ago)).isoformat()

    status = dm_status(
        _settings(**controls), _intensity(dm_allowed=dm_allowed), readiness, started, _NOW
    )

    assert status == expected


@pytest.mark.parametrize("started_at", [None, "not-a-date", "2026-06-13T11:00:00"])
def test_an_unknown_or_naive_start_counts_as_just_started(started_at: str | None) -> None:
    status = dm_status(_settings(), _intensity(dm_allowed=False), _READY, started_at, _NOW)

    assert status == "too_young"


def test_quarantine_names_its_recheck_and_when() -> None:
    record = _record(state="quarantine", quarantine_count=1, next_run_at="2026-06-13T15:00:00Z")

    item = attention(record, _READY)

    assert item is not None
    assert (item.code, item.until, item.attempt) == ("quarantine", "2026-06-13T15:00:00Z", 2)
    assert item.attempts == settings.warming.quarantine_max_repeats


def test_a_flood_wait_names_its_deadline() -> None:
    record = _record(
        state="flood_wait", flood_wait_until="2026-06-13T14:30:00Z", next_run_at="later"
    )

    item = attention(record, _NOT_READY)

    assert item is not None
    assert (item.code, item.until) == ("flood_wait", "2026-06-13T14:30:00Z")


@pytest.mark.parametrize(
    ("last_event", "code"),
    [
        ("stop_timeout", "stopping"),
        ("restart_stopping", "stopping"),
        ("quarantine_exhausted", "quarantine_exhausted"),
        ("quarantine_unreadable", "quarantine_unreadable"),
    ],
)
def test_a_named_error_outranks_readiness(last_event: str, code: str) -> None:
    item = attention(_record(state="error", last_event=last_event), _NOT_READY)

    assert item is not None
    assert item.code == code


def test_readiness_outranks_a_plain_error_and_carries_its_reasons() -> None:
    item = attention(_record(state="error", last_event="loop_crashed"), _NOT_READY)

    assert item is not None
    assert (item.code, item.reasons) == ("not_ready", ["no proxy"])

    plain = attention(_record(state="error", last_event="loop_crashed"), _READY)
    assert plain is not None
    assert plain.code == "error"


def test_a_healthy_card_needs_no_attention() -> None:
    assert attention(_record(), _READY) is None
    assert attention(None, _READY) is None


def test_geo_compares_the_phone_with_the_proxy_country() -> None:
    mexican = _account(phone="+525512345678", proxy_country_code="us")

    geo = account_geo(mexican)

    assert geo.status == "mismatch"
    assert geo_pair(geo) == "MX>US"
    assert geo_accepted(geo, _record(geo_ack="MX>US")) is True
    assert geo_accepted(geo, _record(geo_ack="MX>DE")) is False
    assert geo_accepted(geo, None) is False


def test_a_matching_or_unknown_geo_has_nothing_to_accept() -> None:
    matching = account_geo(_account(phone="+525512345678", proxy_country_code="MX"))
    unknown = account_geo(_account(phone="+525512345678"))

    assert geo_pair(matching) is None
    assert geo_pair(unknown) is None
    assert geo_accepted(matching, _record(geo_ack="MX>MX")) is False
