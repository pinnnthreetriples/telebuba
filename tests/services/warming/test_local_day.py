"""The warming day follows the account's local calendar, not UTC."""

from __future__ import annotations

import random
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from core.config import settings
from core.db import create_account, fetch_warming_state, save_warming_settings, upsert_warming_state
from schemas.accounts import AccountCreate
from schemas.warming import WarmingStateRecord, WarmingStateWrite
from services import warming
from services.warming import _fleet, _iteration, _loop, _seams
from services.warming._local_day import _local_date, _next_local_wake
from tests.services.warming._support import _no_quiet_days, _Recorder, _seed_channel

_MEXICO = "America/Mexico_City"  # UTC-6, no DST since 2022.
# 01:00 UTC on Saturday is still Friday evening in Mexico City.
_SAT_01_UTC = datetime(2026, 6, 13, 1, 0, tzinfo=UTC)


def _record(daily_date: str | None, count: int) -> WarmingStateRecord:
    return WarmingStateRecord(
        account_id="a",
        state="sleeping",
        updated_at="t",
        daily_actions=count,
        daily_count_date=daily_date,
    )


def _active_hours(monkeypatch: pytest.MonkeyPatch, spread_minutes: int) -> None:
    monkeypatch.setattr(settings.warming, "active_hours_enabled", True)
    monkeypatch.setattr(settings.warming, "active_hours_start", 8)
    monkeypatch.setattr(settings.warming, "active_hours_end", 23)
    monkeypatch.setattr(settings.warming, "active_hours_start_spread_minutes", spread_minutes)


def test_local_date_is_the_previous_day_west_of_utc_after_utc_midnight() -> None:
    assert _local_date(_MEXICO, _SAT_01_UTC) == "2026-06-12"


@pytest.mark.parametrize("tz_name", [None, "Not/AZone"])
def test_unknown_time_zone_falls_back_to_the_utc_date(tz_name: str | None) -> None:
    assert _local_date(tz_name, _SAT_01_UTC) == "2026-06-13"


def test_daily_counter_is_not_reset_before_local_midnight() -> None:
    record = _record("2026-06-12", 7)

    assert warming._roll_daily(record, _local_date(_MEXICO, _SAT_01_UTC)) == (7, "2026-06-12")


def test_future_dated_stored_day_counts_as_the_same_local_day() -> None:
    # Written under the UTC day (already Saturday) while Mexico City is still on Friday.
    record = _record("2026-06-13", 7)

    assert warming._roll_daily(record, "2026-06-12") == (7, "2026-06-12")


@pytest.mark.parametrize("stored", ["2026-06-14", "2026-06-11", "garbage"])
def test_any_other_stored_day_resets_the_counter(stored: str) -> None:
    assert warming._roll_daily(_record(stored, 7), "2026-06-12") == (0, "2026-06-12")


def test_wake_is_the_next_local_morning_not_utc_midnight(monkeypatch: pytest.MonkeyPatch) -> None:
    _active_hours(monkeypatch, spread_minutes=90)

    wake = _next_local_wake(_MEXICO, _SAT_01_UTC, random.Random(3), "acc-1")  # noqa: S311

    local = wake.astimezone(ZoneInfo(_MEXICO))
    morning = datetime.combine(local.date(), time(8), tzinfo=ZoneInfo(_MEXICO))
    assert wake.utcoffset() == timedelta(0)
    assert local.date().isoformat() == "2026-06-13"  # the next local day
    assert morning <= local <= morning + timedelta(minutes=90)
    assert (wake.hour, wake.minute, wake.second) != (0, 0, 0)


def test_wakes_spread_across_the_fleet(monkeypatch: pytest.MonkeyPatch) -> None:
    _active_hours(monkeypatch, spread_minutes=90)
    rng = random.Random(5)  # noqa: S311 - deterministic draw

    wakes = {_next_local_wake(_MEXICO, _SAT_01_UTC, rng, f"acc-{i}") for i in range(20)}

    assert len(wakes) == 20


def test_wake_with_unknown_zone_is_the_next_utc_morning(monkeypatch: pytest.MonkeyPatch) -> None:
    _active_hours(monkeypatch, spread_minutes=0)

    wake = _next_local_wake(None, _SAT_01_UTC, random.Random(1), "acc-1")  # noqa: S311

    assert wake == datetime(2026, 6, 14, 8, 0, tzinfo=UTC)


def test_quiet_day_is_decided_by_the_local_date(monkeypatch: pytest.MonkeyPatch) -> None:
    # Weekdays never rest, weekends always do: Friday evening local is not quiet
    # even though the UTC date is already Saturday.
    monkeypatch.setattr(settings.warming, "quiet_day_weekday_probability", 0.0)
    monkeypatch.setattr(settings.warming, "quiet_day_weekend_probability", 1.0)

    assert not _fleet._is_quiet_day("acc-1", _local_date(_MEXICO, _SAT_01_UTC))
    assert _fleet._is_quiet_day("acc-1", _SAT_01_UTC.date().isoformat())


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz: object = None) -> _FrozenDatetime:  # noqa: ARG003 - frozen instant.
        return cls.fromtimestamp(_SAT_01_UTC.timestamp(), UTC)


@pytest.mark.asyncio
async def test_iteration_keeps_the_local_day_budget_and_wakes_in_the_local_morning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_quiet_days(monkeypatch)
    _active_hours(monkeypatch, spread_minutes=60)
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    monkeypatch.setattr(_iteration, "datetime", _FrozenDatetime)

    async def mexico(_account_id: str) -> str:
        return _MEXICO

    monkeypatch.setattr(_loop, "_account_tz", mexico)
    await _seed_channel()
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="sleeping",
            daily_actions=settings.warming.phase_daily_cap["intro"],
            daily_count_date="2026-06-12",  # Friday: still today in Mexico City.
        ),
    )

    result = await warming.run_loop_iteration("acc-1")

    assert result.detail == "daily limit"
    assert recorder.actions == []
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.daily_count_date == "2026-06-12"
    assert record.next_run_at is not None
    wake = datetime.fromisoformat(record.next_run_at).astimezone(ZoneInfo(_MEXICO))
    assert wake.date().isoformat() == "2026-06-13"
    assert time(8) <= wake.time() <= time(9)
