"""Fleet DM breaker (Прогрев 2.0, 1.5): two DM spam blocks in a day pause every DM."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.config import settings
from core.db import (
    create_account,
    load_warming_settings,
    oldest_unreplied_for,
    record_dialogue_message,
)
from core.repositories.warming_dm_pause import store_dm_paused_until
from core.repositories.warming_floods import record_warming_flood_event
from schemas.accounts import AccountCreate
from schemas.gemini import GeminiResult
from schemas.warming import WarmingCycleRequest, WarmingCycleResult
from schemas.warming_flood import WarmingFloodEventWrite
from services import warming
from services.warming import _dm_breaker, _loop, _seams
from services.warming._dm_breaker import active_dm_pause, note_dm_flood, trip_dm_breaker
from tests.services.warming._support import (
    _no_quiet_days,
    _Recorder,
    _seed_channel,
    _seed_two_warming_accounts,
    _set_settings,
)

_NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


async def _flood(hours_ago: float, action: str = "send_dm", account_id: str = "acc-1") -> None:
    await record_warming_flood_event(
        WarmingFloodEventWrite(
            account_id=account_id,
            created_at=(_NOW - timedelta(hours=hours_ago)).isoformat(),
            action=action,
        ),
    )


async def _stored_pause() -> str | None:
    return (await load_warming_settings()).dm_paused_until


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, object]]]:
    logged: list[tuple[str, dict[str, object]]] = []

    async def capture(
        _level: str, event: str, *, extra: dict[str, object] | None = None, **_kwargs: object
    ) -> None:
        logged.append((event, dict(extra or {})))

    monkeypatch.setattr(_dm_breaker, "log_event", capture)
    return logged


@pytest.mark.asyncio
async def test_two_dm_blocks_in_a_day_pause_dms_for_72_hours(
    events: list[tuple[str, dict[str, object]]],
) -> None:
    await _flood(20, account_id="acc-1")
    await _flood(1, account_id="acc-2")

    tripped = await trip_dm_breaker(_NOW)

    until = _NOW + timedelta(hours=72)
    assert tripped == until
    assert await _stored_pause() == until.isoformat()
    assert events == [("warming_dm_fleet_paused", {"until": until.isoformat(), "floods": 2})]


@pytest.mark.asyncio
async def test_one_dm_block_does_not_trip(events: list[tuple[str, dict[str, object]]]) -> None:
    await _flood(1)
    await _flood(2, action="join_channel")

    assert await trip_dm_breaker(_NOW) is None
    assert await _stored_pause() is None
    assert events == []


@pytest.mark.asyncio
async def test_a_dm_block_older_than_the_window_does_not_count(
    events: list[tuple[str, dict[str, object]]],
) -> None:
    await _flood(25)
    await _flood(1)

    assert await trip_dm_breaker(_NOW) is None
    assert events == []


@pytest.mark.asyncio
async def test_a_pause_in_force_is_not_extended(
    events: list[tuple[str, dict[str, object]]],
) -> None:
    paused = (_NOW + timedelta(hours=10)).isoformat()
    await store_dm_paused_until(paused)
    await _flood(2)
    await _flood(1)

    assert await trip_dm_breaker(_NOW) is None
    assert await _stored_pause() == paused
    assert events == []


@pytest.mark.asyncio
async def test_blocks_before_a_lifted_pause_are_spent(
    events: list[tuple[str, dict[str, object]]],
) -> None:
    await _flood(3)
    await _flood(2)
    # The operator lifted the pause an hour ago: the stamp is now in the past.
    await store_dm_paused_until((_NOW - timedelta(hours=1)).isoformat())
    await _flood(0.5)

    assert await trip_dm_breaker(_NOW) is None
    await _flood(0.25)
    assert await trip_dm_breaker(_NOW) == _NOW + timedelta(hours=72)
    assert [event for event, _extra in events] == ["warming_dm_fleet_paused"]


@pytest.mark.asyncio
async def test_threshold_and_durations_come_from_config(
    monkeypatch: pytest.MonkeyPatch,
    events: list[tuple[str, dict[str, object]]],
) -> None:
    monkeypatch.setattr(settings.warming, "dm_breaker_floods", 1)
    monkeypatch.setattr(settings.warming, "dm_breaker_pause_hours", 5.0)
    await _flood(1)

    assert await trip_dm_breaker(_NOW) == _NOW + timedelta(hours=5)
    assert len(events) == 1


def test_active_dm_pause_reads_only_a_future_stamp() -> None:
    future = (_NOW + timedelta(minutes=1)).isoformat()
    assert active_dm_pause(future, _NOW) == _NOW + timedelta(minutes=1)
    assert active_dm_pause(_NOW.isoformat(), _NOW) is None
    assert active_dm_pause(None, _NOW) is None
    assert active_dm_pause("garbage", _NOW) is None


@pytest.mark.asyncio
async def test_note_dm_flood_ignores_other_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[datetime] = []

    async def trip(now: datetime) -> None:
        calls.append(now)

    monkeypatch.setattr(_dm_breaker, "trip_dm_breaker", trip)
    await note_dm_flood("join_channel")
    await note_dm_flood(None)
    assert calls == []
    await note_dm_flood("send_dm")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_note_dm_flood_swallows_a_breaker_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def explode(_now: datetime) -> None:
        msg = "db down"
        raise RuntimeError(msg)

    monkeypatch.setattr(_dm_breaker, "trip_dm_breaker", explode)
    await note_dm_flood("send_dm")


async def _dm_ready_fleet(monkeypatch: pytest.MonkeyPatch, recorder: _Recorder) -> None:
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    monkeypatch.setattr(settings.warming, "dm_min_age_hours", 0.0)

    async def fake_generate(_request: object) -> GeminiResult:
        return GeminiResult(status="ok", text="hi there")

    monkeypatch.setattr(_seams, "generate_text", fake_generate)
    await _seed_channel()
    await _set_settings(chat=True, reactions=False, key="gemini-key")
    await _seed_two_warming_accounts()


@pytest.mark.asyncio
async def test_a_paused_fleet_sends_no_dm(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    await _dm_ready_fleet(monkeypatch, recorder)
    await store_dm_paused_until((datetime.now(UTC) + timedelta(hours=72)).isoformat())

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.messages_sent == 0
    assert "send_dm" not in recorder.types()


@pytest.mark.asyncio
async def test_an_expired_pause_lets_dms_through(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    await _dm_ready_fleet(monkeypatch, recorder)
    await store_dm_paused_until((datetime.now(UTC) - timedelta(minutes=1)).isoformat())

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.messages_sent == 1


@pytest.mark.asyncio
async def test_lift_reopens_dms_and_is_a_no_op_without_a_pause() -> None:
    assert (await warming.lift_dm_pause("admin")).dm_paused_until is None
    assert await _stored_pause() is None

    until = datetime.now(UTC) + timedelta(hours=72)
    await store_dm_paused_until(until.isoformat())
    assert (await warming.load_settings()).dm_paused_until == until.isoformat()

    lifted = await warming.lift_dm_pause("admin")

    assert lifted.dm_paused_until is None
    stored = await _stored_pause()
    assert stored is not None
    assert datetime.fromisoformat(stored) <= datetime.now(UTC)


@pytest.mark.asyncio
async def test_a_dm_spam_block_in_the_loop_trips_the_breaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await record_warming_flood_event(
        WarmingFloodEventWrite(
            account_id="acc-2",
            created_at=(datetime.now(UTC) - timedelta(hours=3)).isoformat(),
            action="send_dm",
        ),
    )

    async def dm_blocked(request: WarmingCycleRequest, **_kwargs: object) -> WarmingCycleResult:
        return WarmingCycleResult(
            account_id=request.account_id,
            status="peer_flood",
            last_failed_action="send_dm",
        )

    _no_quiet_days(monkeypatch)
    monkeypatch.setattr(_loop, "_execute_cycle", dm_blocked)
    await _seed_channel()
    await _set_settings(chat=True, reactions=False, key="")
    await create_account(AccountCreate(account_id="acc-1"))

    await warming.run_loop_iteration("acc-1")

    stored = await _stored_pause()
    assert stored is not None
    assert datetime.fromisoformat(stored) > datetime.now(UTC) + timedelta(hours=71)


async def _pause_while_writing(_request: object) -> GeminiResult:
    # Another account's DM spam block trips the breaker while this one writes.
    await store_dm_paused_until((datetime.now(UTC) + timedelta(hours=72)).isoformat())
    return GeminiResult(status="ok", text="hi there")


@pytest.mark.asyncio
async def test_a_pause_that_starts_mid_cycle_stops_the_opener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()
    await _dm_ready_fleet(monkeypatch, recorder)
    monkeypatch.setattr(_seams, "generate_text", _pause_while_writing)

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.messages_sent == 0
    assert result.failures == 0
    assert "send_dm" not in recorder.types()


@pytest.mark.asyncio
async def test_a_pause_that_starts_mid_cycle_keeps_the_reply_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()
    await _dm_ready_fleet(monkeypatch, recorder)
    monkeypatch.setattr(_seams, "generate_text", _pause_while_writing)
    await record_dialogue_message("acc-2", "acc-1", "hello")

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.messages_sent == 0
    assert "send_dm" not in recorder.types()
    pending = await oldest_unreplied_for("acc-1")
    assert pending is not None
    assert pending.from_account == "acc-2"
