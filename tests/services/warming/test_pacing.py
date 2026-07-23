"""Warming tests split from the former service test module: test_pacing.py."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from core.config import settings
from core.db import (
    create_account,
    fetch_warming_state,
    upsert_warming_state,
)
from schemas.accounts import AccountCreate
from schemas.warming import (
    WarmingCycleResult,
    WarmingStateRecord,
    WarmingStateWrite,
)
from services import warming
from services.warming import _loop, _runner, _seams
from tests.services.warming._support import (
    _MON,
)


def test_in_quiet_hours_disabled_when_equal() -> None:
    assert warming._in_quiet_hours(datetime(2026, 6, 12, 3, tzinfo=UTC), 5, 5) is False


def test_in_quiet_hours_non_wrapping_window() -> None:
    assert warming._in_quiet_hours(datetime(2026, 6, 12, 2, tzinfo=UTC), 1, 5)
    assert not warming._in_quiet_hours(datetime(2026, 6, 12, 6, tzinfo=UTC), 1, 5)


def test_in_quiet_hours_wrapping_midnight() -> None:
    assert warming._in_quiet_hours(datetime(2026, 6, 12, 23, tzinfo=UTC), 23, 7)
    assert warming._in_quiet_hours(datetime(2026, 6, 12, 2, tzinfo=UTC), 23, 7)
    assert not warming._in_quiet_hours(datetime(2026, 6, 12, 12, tzinfo=UTC), 23, 7)


def test_human_delay_is_bounded_and_right_skewed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_seams, "rng", random.Random(7))  # noqa: S311 - deterministic test rng
    samples = [warming._human_delay(0.0, 10.0) for _ in range(2000)]
    assert all(0.0 <= sample <= 10.0 for sample in samples)
    # heavy right tail → most pauses sit below the midpoint, unlike a uniform draw
    below_midpoint = sum(1 for sample in samples if sample < 5.0)
    assert below_midpoint > len(samples) * 0.5
    # an equal range collapses to the value
    assert warming._human_delay(3.0, 3.0) == 3.0


def test_shift_to_active_hours_moves_night_into_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "active_hours_enabled", True)
    monkeypatch.setattr(settings.warming, "active_hours_start", 8)
    monkeypatch.setattr(settings.warming, "active_hours_end", 23)
    monkeypatch.setattr(settings.warming, "active_hours_start_spread_minutes", 0)  # exact snap
    night = datetime(2026, 6, 12, 3, 0, tzinfo=UTC)
    assert warming._shift_to_active_hours(night, None, _seams.rng, "acc-1").hour == 8
    day = datetime(2026, 6, 12, 14, 0, tzinfo=UTC)
    assert warming._shift_to_active_hours(day, None, _seams.rng, "acc-1") == day


def test_shift_to_active_hours_uses_account_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "active_hours_enabled", True)
    monkeypatch.setattr(settings.warming, "active_hours_start", 8)
    monkeypatch.setattr(settings.warming, "active_hours_end", 23)
    # 03:00 UTC is the middle of the night in New York → shifted into the window.
    night = datetime(2026, 6, 12, 3, 0, tzinfo=UTC)
    shifted = warming._shift_to_active_hours(night, "America/New_York", _seams.rng, "acc-1")
    local = shifted.astimezone(ZoneInfo("America/New_York"))
    assert 8 <= local.hour < 23


def test_shift_to_active_hours_bad_timezone_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "active_hours_enabled", True)
    monkeypatch.setattr(settings.warming, "active_hours_start", 8)
    monkeypatch.setattr(settings.warming, "active_hours_end", 23)
    monkeypatch.setattr(settings.warming, "active_hours_start_spread_minutes", 0)  # exact snap
    night = datetime(2026, 6, 12, 3, 0, tzinfo=UTC)
    assert warming._shift_to_active_hours(night, "Not/AZone", _seams.rng, "acc-1").hour == 8


def test_shift_to_active_hours_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "active_hours_start", 0)
    monkeypatch.setattr(settings.warming, "active_hours_end", 0)
    night = datetime(2026, 6, 12, 3, 0, tzinfo=UTC)
    assert warming._shift_to_active_hours(night, None, _seams.rng, "acc-1") == night


def test_shift_to_active_hours_chronotype_stable_but_fleet_varied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # #203: the morning offset is a *stable per-account chronotype* — the same
    # account snaps to the same wall-clock minute day after day (jitter pinned
    # off), while different accounts spread across the band, so the fleet doesn't
    # cluster on one second nor drift day-to-day.
    monkeypatch.setattr(settings.warming, "active_hours_enabled", True)
    monkeypatch.setattr(settings.warming, "active_hours_start", 8)
    monkeypatch.setattr(settings.warming, "active_hours_end", 23)
    monkeypatch.setattr(settings.warming, "active_hours_start_spread_minutes", 120)
    monkeypatch.setattr(settings.warming, "chronotype_jitter_minutes", 0.0)  # isolate the base
    day1 = datetime(2026, 6, 12, 3, 0, tzinfo=UTC)
    day2 = datetime(2026, 6, 13, 3, 0, tzinfo=UTC)
    first = warming._shift_to_active_hours(day1, None, _seams.rng, "acc-1")
    second = warming._shift_to_active_hours(day2, None, _seams.rng, "acc-1")
    assert first.time() == second.time()  # same account, day-to-day consistent
    fleet = {
        warming._shift_to_active_hours(day1, None, _seams.rng, f"acc-{i}").time() for i in range(8)
    }
    assert len(fleet) > 1  # accounts don't all wake on the same wall-clock second
    for offset_time in fleet:
        assert 8 <= offset_time.hour < 10  # inside [08:00, 10:00) = [start, start + spread)


def test_shift_to_active_hours_daily_jitter_wobbles_the_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # With a non-zero jitter the same account's snapped time wobbles a little from
    # day to day (a soft draw, not the frozen chronotype base).
    monkeypatch.setattr(settings.warming, "active_hours_enabled", True)
    monkeypatch.setattr(settings.warming, "active_hours_start", 8)
    monkeypatch.setattr(settings.warming, "active_hours_end", 23)
    monkeypatch.setattr(settings.warming, "active_hours_start_spread_minutes", 120)
    monkeypatch.setattr(settings.warming, "chronotype_jitter_minutes", 20.0)
    # Restore a live rng.random() (the fixture pins it to 0.0, collapsing the
    # triangular jitter to a single value).
    monkeypatch.setattr(_seams.rng, "random", random.random)
    night = datetime(2026, 6, 12, 3, 0, tzinfo=UTC)
    draws = {warming._shift_to_active_hours(night, None, _seams.rng, "acc-1") for _ in range(20)}
    assert len(draws) > 1  # daily wobble around the stable base
    for shifted in draws:
        assert 8 <= shifted.hour < 11  # base (≤10:00) ± 20min jitter stays in-band


def test_morning_offset_clamped_to_window_width(monkeypatch: pytest.MonkeyPatch) -> None:
    # #203 P3: an extreme spread must not push a resume past active_hours_end —
    # the offset is clamped to the window width. Window 08:00–10:00 (2h = 120min),
    # spread 600min → clamped to 120, so the snap never exceeds 10:00.
    monkeypatch.setattr(settings.warming, "active_hours_enabled", True)
    monkeypatch.setattr(settings.warming, "active_hours_start", 8)
    monkeypatch.setattr(settings.warming, "active_hours_end", 10)
    monkeypatch.setattr(settings.warming, "active_hours_start_spread_minutes", 600)
    monkeypatch.setattr(settings.warming, "chronotype_jitter_minutes", 0.0)
    night = datetime(2026, 6, 12, 3, 0, tzinfo=UTC)
    for i in range(12):
        shifted = warming._shift_to_active_hours(night, None, _seams.rng, f"acc-{i}")
        assert night.replace(hour=8) <= shifted <= night.replace(hour=10)


@pytest.mark.parametrize("day", [(2026, 3, 8), (2026, 11, 1)])
def test_shift_to_active_hours_survives_dst(
    day: tuple[int, int, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    # DST spring-forward (2026-03-08) and fall-back (2026-11-01) in New York: a
    # night snap must not crash on a non-existent/ambiguous wall-clock hour and
    # must land in the active window in the future.
    monkeypatch.setattr(settings.warming, "active_hours_enabled", True)
    monkeypatch.setattr(settings.warming, "active_hours_start", 8)
    monkeypatch.setattr(settings.warming, "active_hours_end", 23)
    tz = ZoneInfo("America/New_York")
    year, month, dom = day
    night = datetime(year, month, dom, 4, 0, tzinfo=tz).astimezone(UTC)  # 04:00 local, night
    result = warming._shift_to_active_hours(night, "America/New_York", _seams.rng, "acc-1")
    local = result.astimezone(tz)
    assert 8 <= local.hour < 23
    assert result > night


def test_seconds_until_future_and_past() -> None:
    now = datetime(2026, 6, 12, 0, 0, tzinfo=UTC)
    future = (now + timedelta(seconds=120)).isoformat()
    assert warming._seconds_until(future, now) == pytest.approx(120)
    past = (now - timedelta(seconds=50)).isoformat()
    assert warming._seconds_until(past, now) == 0.0


def test_seconds_until_invalid_and_naive() -> None:
    now = datetime(2026, 6, 12, 0, 0, tzinfo=UTC)
    assert warming._seconds_until("not-a-date", now) == 0.0
    # Naive timestamp is treated as UTC rather than crashing.
    assert warming._seconds_until("2026-06-12T00:01:00", now) == pytest.approx(60)


@pytest.mark.asyncio
async def test_initial_delay_respects_future_next_run() -> None:
    now = datetime(2026, 6, 12, 0, 0, tzinfo=UTC)
    record = WarmingStateRecord(
        account_id="a",
        state="sleeping",
        updated_at="t",
        next_run_at=(now + timedelta(seconds=3600)).isoformat(),
    )
    assert await warming._initial_delay_seconds("a", record, now) == pytest.approx(3600)


@pytest.mark.asyncio
async def test_initial_delay_cold_start_shifts_into_window(monkeypatch: pytest.MonkeyPatch) -> None:
    # FIX #10: a cold start (no schedule) at 03:00 local must not fire in the
    # middle of the night — it is routed through the active-hours window.
    monkeypatch.setattr(settings.warming, "active_hours_enabled", True)
    monkeypatch.setattr(settings.warming, "active_hours_start", 8)
    monkeypatch.setattr(settings.warming, "active_hours_end", 23)

    async def fake_tz(_account_id: str) -> str:
        return "Europe/Istanbul"  # UTC+3, no DST

    monkeypatch.setattr(_runner, "_account_tz", fake_tz)
    now = datetime(2026, 6, 12, 0, 0, tzinfo=UTC)  # 03:00 Istanbul → night
    delay = await warming._initial_delay_seconds("acc-1", None, now)
    first_run = (now + timedelta(seconds=delay)).astimezone(ZoneInfo("Europe/Istanbul"))
    assert 8 <= first_run.hour < 23


@pytest.mark.asyncio
async def test_initial_delay_cold_start_spreads_over_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    # FIX #10: cold-start delays vary across accounts and can exceed the old ~8s
    # startup jitter (spread over cold_start_spread_hours). Active hours off so the
    # spread itself is under test, not the window snap.
    monkeypatch.setattr(settings.warming, "active_hours_enabled", False)
    monkeypatch.setattr(settings.warming, "cold_start_spread_hours", 4.0)
    # Restore a live rng.random() (the fixture pins it to 0.0, which would collapse
    # the cold-start uniform() spread to a single value).
    monkeypatch.setattr(_seams.rng, "random", random.random)

    async def fake_tz(_account_id: str) -> None:
        return None

    monkeypatch.setattr(_runner, "_account_tz", fake_tz)
    now = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)
    delays = {await warming._initial_delay_seconds("acc-1", None, now) for _ in range(20)}
    assert len(delays) > 1  # independent draws, not a synchronized burst
    assert max(delays) > 8.0  # spread over hours, not the old few-second jitter


@pytest.mark.asyncio
async def test_initial_delay_cold_start_spans_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    # The default cold-start spread fans a bulk import across its whole window
    # (so the first cycles land the same evening / by next morning, not all at
    # once). Active hours off so the raw spread is under test, not the window snap.
    monkeypatch.setattr(settings.warming, "active_hours_enabled", False)
    monkeypatch.setattr(_seams.rng, "random", random.random)  # live draw over the span

    async def fake_tz(_account_id: str) -> None:
        return None

    monkeypatch.setattr(_runner, "_account_tz", fake_tz)
    now = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)
    span = settings.warming.cold_start_spread_hours * 3600
    delays = [await warming._initial_delay_seconds("acc-1", None, now) for _ in range(50)]

    assert max(delays) > 0.75 * span  # reaches the upper part of the window, not just early
    assert all(0 <= d <= span for d in delays)


@pytest.mark.asyncio
async def test_cold_start_schedule_persists_first_run() -> None:
    # The pre-start hold must write the computed first-cycle time so the card can
    # show a real countdown (instead of a blinking "subscribe" with no target).
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="acc-1", state="active", run_id="run-a"),
    )
    record = await fetch_warming_state("acc-1")
    now = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)

    await _runner._persist_cold_start_schedule("acc-1", record, 3600.0, now, "run-a")

    state = await fetch_warming_state("acc-1")
    assert state is not None
    assert state.next_run_at == (now + timedelta(seconds=3600)).isoformat()
    assert state.last_action is None  # still a hold, not mid-cycle


@pytest.mark.asyncio
async def test_cold_start_schedule_noop_when_already_scheduled() -> None:
    # A restart that already has a future schedule must not be re-rolled.
    await create_account(AccountCreate(account_id="acc-1"))
    scheduled = (datetime(2026, 6, 12, 12, 0, tzinfo=UTC) + timedelta(hours=5)).isoformat()
    await upsert_warming_state(
        WarmingStateWrite(account_id="acc-1", state="active", next_run_at=scheduled),
    )
    record = await fetch_warming_state("acc-1")
    now = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)

    await _runner._persist_cold_start_schedule("acc-1", record, 3600.0, now, None)

    state = await fetch_warming_state("acc-1")
    assert state is not None
    assert state.next_run_at == scheduled  # untouched


def test_loop_sleep_respects_future_next_run() -> None:
    now = datetime(2026, 6, 12, 0, 0, tzinfo=UTC)
    record = WarmingStateRecord(
        account_id="a",
        state="sleeping",
        updated_at="t",
        next_run_at=(now + timedelta(seconds=900)).isoformat(),
    )
    assert warming._loop_sleep_seconds(record, now) == pytest.approx(900)


def test_loop_sleep_falls_back_without_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    # No persisted schedule (shouldn't happen after run_loop_iteration writes one)
    # → a persona-paced gap, not a crash. Assert it's a sane positive duration.
    monkeypatch.setattr(settings.warming, "next_run_jitter_fraction", 0.0)
    value = warming._loop_sleep_seconds(None, datetime(2026, 6, 12, 0, 0, tzinfo=UTC))
    assert 0.0 < value <= 24 * 3600


@pytest.mark.asyncio
async def test_daily_cap_park_shifts_into_active_hours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Post-cap wake honours active hours, not a bare 00:00 UTC instant (#99)."""
    monkeypatch.setattr(settings.warming, "active_hours_enabled", True)
    monkeypatch.setattr(settings.warming, "active_hours_start", 8)
    monkeypatch.setattr(settings.warming, "active_hours_end", 23)
    monkeypatch.setattr(settings.warming, "active_hours_start_spread_minutes", 0)  # exact snap

    async def fake_tz(_account_id: str) -> str:
        return "Europe/Istanbul"  # UTC+3, no DST

    monkeypatch.setattr(_loop, "_account_tz", fake_tz)
    await create_account(AccountCreate(account_id="acc-1"))
    now = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)

    result = await _loop._gate_daily_limit("acc-1", 3, (3, "2026-06-12"), now, run_id=None)

    assert result is not None
    assert result.detail == "daily limit"
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.next_run_at is not None
    parked = datetime.fromisoformat(record.next_run_at)
    # 00:00 UTC is 03:00 in Istanbul (outside 8-23) → shifted forward to 08:00 local.
    assert parked != _loop._next_utc_midnight(now)
    assert parked.astimezone(ZoneInfo("Europe/Istanbul")).hour == 8


@pytest.mark.asyncio
async def test_gate_quiet_day_parks_until_tomorrow(monkeypatch: pytest.MonkeyPatch) -> None:
    # A quiet day parks the account in sleeping until the next day (like the daily
    # cap gate), so the loop resumes and re-evaluates for the fresh calendar day.
    monkeypatch.setattr(settings.warming, "quiet_day_weekday_probability", 1.0)
    monkeypatch.setattr(settings.warming, "active_hours_enabled", False)
    await create_account(AccountCreate(account_id="acc-1"))
    now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)  # Monday

    result = await _loop._gate_quiet_day("acc-1", (0, _MON), now, run_id=None)

    assert result is not None
    assert result.detail == "quiet day"
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.state == "sleeping"
    assert record.last_event == "quiet_day"
    assert record.next_run_at is not None
    assert datetime.fromisoformat(record.next_run_at) >= _loop._next_utc_midnight(now)


@pytest.mark.asyncio
async def test_gate_quiet_day_passes_through_on_active_day(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "quiet_day_weekday_probability", 0.0)
    monkeypatch.setattr(settings.warming, "quiet_day_weekend_probability", 0.0)
    await create_account(AccountCreate(account_id="acc-1"))
    now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)

    assert await _loop._gate_quiet_day("acc-1", (0, _MON), now, run_id=None) is None


@pytest.mark.asyncio
async def test_run_loop_iteration_skips_on_quiet_day(monkeypatch: pytest.MonkeyPatch) -> None:
    # The loop honours a quiet day: no cycle runs and the account parks as quiet.
    monkeypatch.setattr(settings.warming, "quiet_day_weekday_probability", 1.0)
    monkeypatch.setattr(settings.warming, "quiet_day_weekend_probability", 1.0)
    await create_account(AccountCreate(account_id="acc-1"))

    ran: list[int] = []

    async def fake_cycle(*_args: object, **_kwargs: object) -> WarmingCycleResult:
        ran.append(1)
        return WarmingCycleResult(account_id="acc-1", status="ok")

    monkeypatch.setattr(_loop, "run_one_cycle", fake_cycle)

    result = await warming.run_loop_iteration("acc-1")

    assert result.detail == "quiet day"
    assert ran == []  # the cycle never ran
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.last_event == "quiet_day"
