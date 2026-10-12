"""Warming days are days of work: what counts a local date, and what never does."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.config import settings
from core.db import (
    count_warmed_day,
    create_account,
    fetch_warming_state,
    list_recent_logs,
    upsert_warming_state,
)
from schemas.accounts import AccountCreate
from schemas.warming import StartWarmingRequest, WarmingCycleResult, WarmingStateWrite
from services import warming
from services.warming import _runtime, _seams
from services.warming._worked_days import _cycle_worked
from services.warming.board import load_board
from tests.services.warming._support import (
    _fake_loop,
    _no_quiet_days,
    _Recorder,
    _seed_ready_account,
    _set_settings,
)


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


async def _seed_row(state: str = "sleeping", **fields: object) -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(
        WarmingStateWrite.model_validate({"account_id": "acc-1", "state": state, **fields}),
    )


async def _warmed_days() -> int:
    record = await fetch_warming_state("acc-1")
    assert record is not None
    return record.warmed_days


@pytest.mark.asyncio
async def test_a_local_date_counts_once() -> None:
    await _seed_row()

    assert await count_warmed_day("acc-1", "2026-10-01") == 1
    assert await count_warmed_day("acc-1", "2026-10-01") is None
    assert await count_warmed_day("acc-1", "2026-10-02") == 2

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.last_warmed_date == "2026-10-02"


@pytest.mark.asyncio
async def test_a_date_before_the_last_counted_one_is_refused() -> None:
    # A legacy row whose day was stored one day ahead must not count that day twice.
    await _seed_row()
    assert await count_warmed_day("acc-1", "2026-10-02") == 1

    assert await count_warmed_day("acc-1", "2026-10-01") is None
    assert await _warmed_days() == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["error", "quarantine", "flood_wait", "idle"])
async def test_a_day_in_a_non_working_state_does_not_count(state: str) -> None:
    await _seed_row(state)

    assert await count_warmed_day("acc-1", "2026-10-01") is None
    assert await _warmed_days() == 0


@pytest.mark.asyncio
async def test_a_retired_generation_cannot_count_a_day() -> None:
    await _seed_row(run_id="gen-new")

    assert await count_warmed_day("acc-1", "2026-10-01", expected_run_id="gen-old") is None
    assert await count_warmed_day("acc-1", "2026-10-01", expected_run_id="gen-new") == 1


@pytest.mark.parametrize(
    ("result", "worked"),
    [
        (WarmingCycleResult(account_id="a", status="ok"), True),
        (WarmingCycleResult(account_id="a", status="failed", channels_read=1), True),
        (WarmingCycleResult(account_id="a", status="failed"), False),
        (WarmingCycleResult(account_id="a", status="flood_wait", channels_read=3), False),
        (WarmingCycleResult(account_id="a", status="peer_flood", channels_read=3), False),
        (WarmingCycleResult(account_id="a", status="skipped"), False),
    ],
)
def test_only_a_cycle_that_did_work_earns_the_day(
    result: WarmingCycleResult,
    *,
    worked: bool,
) -> None:
    assert _cycle_worked(result) is worked


@pytest.mark.asyncio
async def test_a_successful_cycle_counts_its_local_day_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_quiet_days(monkeypatch)
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_ready_account("acc-1")
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="sleeping",
            started_at=datetime.now(UTC).isoformat(),
            target_days=7,
        ),
    )

    first = await warming.run_loop_iteration("acc-1")
    second = await warming.run_loop_iteration("acc-1")

    assert (first.status, second.status) == ("ok", "ok")
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.warmed_days == 1
    assert record.last_warmed_date == _today()
    counted = [e for e in await list_recent_logs(limit=100) if e.event == "warming_day_counted"]
    assert [e.extra for e in counted] == [{"warmed_days": 1, "date": _today()}]


@pytest.mark.asyncio
async def test_a_flooded_cycle_does_not_count_its_day(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_quiet_days(monkeypatch)
    recorder = _Recorder()
    recorder.flood_on = {"set_online"}
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_ready_account("acc-1")
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="sleeping"))

    result = await warming.run_loop_iteration("acc-1")

    assert result.status == "flood_wait"
    assert await _warmed_days() == 0


@pytest.mark.asyncio
async def test_a_planned_quiet_day_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "quiet_day_weekday_probability", 1.0)
    monkeypatch.setattr(settings.warming, "quiet_day_weekend_probability", 1.0)
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_ready_account("acc-1")
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="sleeping"))

    result = await warming.run_loop_iteration("acc-1")

    assert result.detail == "quiet day"
    assert recorder.actions == []
    assert await _warmed_days() == 1


@pytest.mark.asyncio
async def test_the_target_gate_waits_for_worked_days_not_the_calendar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Ten calendar days in, but only two of them worked: a target of three is not met.
    _no_quiet_days(monkeypatch)
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_ready_account("acc-1")
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="sleeping",
            started_at=(datetime.now(UTC) - timedelta(days=10)).isoformat(),
            target_days=3,
            warmed_days=2,
            last_warmed_date="2026-01-01",
        ),
    )

    result = await warming.run_loop_iteration("acc-1")

    assert result.status == "ok"
    assert "set_online" in recorder.types()
    assert await _warmed_days() == 3


@pytest.mark.asyncio
async def test_a_start_from_idle_opens_a_fresh_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_runtime, "_warming_loop", _fake_loop)
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)
    await _seed_row("idle", warmed_days=5, last_warmed_date="2026-10-01")

    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert (record.warmed_days, record.last_warmed_date) == (0, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["sleeping", "error", "quarantine"])
async def test_a_restart_keeps_the_count(monkeypatch: pytest.MonkeyPatch, state: str) -> None:
    monkeypatch.setattr(_runtime, "_warming_loop", _fake_loop)
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)
    await _seed_row(
        state,
        started_at=datetime.now(UTC).isoformat(),
        warmed_days=5,
        last_warmed_date="2026-10-01",
    )

    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert (record.warmed_days, record.last_warmed_date) == (5, "2026-10-01")


@pytest.mark.asyncio
async def test_the_board_card_shows_worked_and_idle_days() -> None:
    await _seed_row(
        started_at=(datetime.now(UTC) - timedelta(days=7, hours=1)).isoformat(),
        target_days=7,
        warmed_days=5,
        last_warmed_date="2026-10-01",
    )

    board = await load_board()

    card = next(card for card in board.warming if card.account_id == "acc-1")
    assert (card.warming_days, card.warmed_days, card.idle_days) == (7, 5, 2)


@pytest.mark.asyncio
async def test_idle_days_never_go_negative() -> None:
    await _seed_row(
        started_at=datetime.now(UTC).isoformat(),
        warmed_days=1,
        last_warmed_date=_today(),
    )

    board = await load_board()

    card = next(card for card in board.warming if card.account_id == "acc-1")
    assert (card.warming_days, card.warmed_days, card.idle_days) == (0, 1, 0)
