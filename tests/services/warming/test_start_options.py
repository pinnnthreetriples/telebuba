"""The operator-chosen start delay: when the first warming cycle lands."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import create_account, fetch_warming_state, save_warming_settings
from schemas._warming_start import StartPreviewRequest
from schemas.accounts import AccountCreate
from schemas.warming import StartWarmingRequest, WarmingStateRecord
from services import warming
from services.warming import _runtime, _seams, _start_options
from services.warming._start_options import first_run_at, preview_start
from tests.factories import AccountReadFactory
from tests.services.warming._support import _fake_loop

if TYPE_CHECKING:
    from schemas.accounts import AccountRead

# 20:00 UTC: an hour of daylight left under an 08-23 active window in UTC.
_EVENING = datetime(2026, 6, 13, 20, 0, tzinfo=UTC)
_NEXT_MORNING = datetime(2026, 6, 14, 8, 0, tzinfo=UTC)


class _Frozen(datetime):
    @classmethod
    def now(cls, tz: object = None) -> datetime:  # noqa: ARG003
        return _EVENING


@pytest.fixture
def frozen_evening(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_start_options, "datetime", _Frozen)
    monkeypatch.setattr(settings.warming, "active_hours_enabled", True)
    monkeypatch.setattr(settings.warming, "active_hours_start", 8)
    monkeypatch.setattr(settings.warming, "active_hours_end", 23)
    # No chronotype band: a shifted moment lands on the window's first minute.
    monkeypatch.setattr(settings.warming, "active_hours_start_spread_minutes", 0)


def _account(account_id: str = "acc-1") -> AccountRead:
    return AccountReadFactory.build(account_id=account_id, phone=None)


def _request(hours: int | None, *, spread: bool = False) -> StartWarmingRequest:
    return StartWarmingRequest(account_id="acc-1", start_delay_hours=hours, spread_start=spread)


@pytest.mark.usefixtures("frozen_evening")
def test_single_start_lands_at_the_end_of_the_delay() -> None:
    at = first_run_at(_account(), _request(2))

    assert at == (_EVENING + timedelta(hours=2)).isoformat()


@pytest.mark.usefixtures("frozen_evening")
def test_a_night_time_first_cycle_moves_to_the_local_morning() -> None:
    # 20:00 + 6h is 02:00 UTC, inside the account's night.
    at = first_run_at(_account(), _request(6))

    assert at == _NEXT_MORNING.isoformat()


@pytest.mark.usefixtures("frozen_evening")
def test_the_night_rule_uses_the_account_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    # Tokyo is UTC+9: 20:00 + 2h UTC is 07:00 there, an hour before its morning.
    monkeypatch.setattr(_start_options, "timezone_for_phone", lambda _phone: "Asia/Tokyo")

    at = first_run_at(_account(), _request(2))

    assert at == datetime(2026, 6, 13, 23, 0, tzinfo=UTC).isoformat()


def test_omitted_delay_leaves_the_cold_start_fallback() -> None:
    assert first_run_at(_account(), _request(None)) is None


@pytest.mark.usefixtures("frozen_evening")
def test_pool_start_spreads_each_account_inside_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.warming, "active_hours_enabled", False)
    # The shared fixture pins ``random()`` to 0; a pool needs a real draw per account.
    monkeypatch.setattr(_seams, "rng", random.Random(7))  # noqa: S311 - deterministic test

    moments = [
        datetime.fromisoformat(
            first_run_at(_account(f"acc-{n}"), _request(3, spread=True)) or "",
        )
        for n in range(8)
    ]

    assert len(set(moments)) == len(moments)
    assert all(_EVENING <= at <= _EVENING + timedelta(hours=3) for at in moments)


@pytest.mark.asyncio
async def test_initial_wait_honours_a_chosen_delay_past_the_cold_start_cap() -> None:
    now = datetime.now(UTC)
    chosen = now + timedelta(hours=20)
    record = WarmingStateRecord(
        account_id="acc-1", state="active", updated_at="t", next_run_at=chosen.isoformat()
    )

    delay = await warming._initial_delay_seconds("acc-1", record, now)

    assert delay > settings.warming.cold_start_spread_hours * 3600
    assert delay == pytest.approx(20 * 3600, abs=1)


async def _start(monkeypatch: pytest.MonkeyPatch, request: StartWarmingRequest) -> str | None:
    monkeypatch.setattr(_runtime, "_warming_loop", _fake_loop)
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )
    await create_account(AccountCreate(account_id=request.account_id))
    await warming.start_warming(request)
    record = await fetch_warming_state(request.account_id)
    assert record is not None
    return record.next_run_at


@pytest.mark.asyncio
async def test_start_persists_the_chosen_first_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "active_hours_enabled", False)
    before = datetime.now(UTC)

    stored = await _start(monkeypatch, _request(4))

    assert stored is not None
    expected = before + timedelta(hours=4)
    assert expected <= datetime.fromisoformat(stored) <= expected + timedelta(minutes=1)


@pytest.mark.asyncio
async def test_start_without_a_delay_keeps_the_schedule_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert await _start(monkeypatch, _request(None)) is None


@pytest.mark.asyncio
@pytest.mark.usefixtures("frozen_evening")
async def test_preview_of_a_single_start_names_one_moment() -> None:
    preview = await preview_start(StartPreviewRequest(account_ids=["ghost"], start_delay_hours=1))

    (item,) = preview.items
    assert item.time_zone == "UTC"
    assert item.earliest_at == item.latest_at == (_EVENING + timedelta(hours=1)).isoformat()
    assert item.shifted is False


@pytest.mark.asyncio
@pytest.mark.usefixtures("frozen_evening")
async def test_preview_of_a_night_start_says_it_moved_to_the_morning() -> None:
    preview = await preview_start(StartPreviewRequest(account_ids=["ghost"], start_delay_hours=6))

    (item,) = preview.items
    assert item.latest_at == _NEXT_MORNING.isoformat()
    assert item.shifted is True


@pytest.mark.asyncio
@pytest.mark.usefixtures("frozen_evening")
async def test_preview_of_a_pool_start_spans_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _start_options,
        "timezone_for_phone",
        lambda phone: "Asia/Tokyo" if phone else None,
    )
    await create_account(AccountCreate(account_id="tokyo", phone="tokyo-line"))

    preview = await preview_start(
        StartPreviewRequest(account_ids=["ghost", "tokyo"], start_delay_hours=2, spread_start=True)
    )

    utc, tokyo = preview.items
    # 20:00-22:00 UTC is all daytime in UTC; in Tokyo it is 05:00-07:00, all night.
    assert (utc.earliest_at, utc.latest_at, utc.shifted) == (
        _EVENING.isoformat(),
        (_EVENING + timedelta(hours=2)).isoformat(),
        False,
    )
    assert tokyo.time_zone == "Asia/Tokyo"
    assert (
        tokyo.earliest_at == tokyo.latest_at == datetime(2026, 6, 13, 23, 0, tzinfo=UTC).isoformat()
    )
    assert tokyo.shifted is True


@pytest.mark.asyncio
@pytest.mark.usefixtures("frozen_evening")
async def test_preview_ignores_the_night_rule_when_it_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.warming, "active_hours_enabled", False)

    preview = await preview_start(
        StartPreviewRequest(account_ids=["ghost"], start_delay_hours=12, spread_start=True)
    )

    assert preview.items[0].shifted is False


def test_preview_draws_the_daily_wobble_at_its_centre() -> None:
    rng = _start_options._CentredRandom()

    assert rng.triangular(-30.0, 30.0, 0.0) == 0.0
    assert rng.triangular(2.0, 4.0) == 3.0
