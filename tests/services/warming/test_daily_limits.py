"""Warming tests split from the former service test module: test_daily_limits.py."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.config import settings
from core.db import (
    create_account,
    fetch_warming_state,
    save_warming_settings,
    upsert_warming_state,
)
from schemas.accounts import AccountCreate
from schemas.telegram_actions import ActionResult, TelegramAction
from schemas.warming import (
    WarmingCycleRequest,
    WarmingStateRecord,
    WarmingStateWrite,
)
from services import warming
from services.warming import _loop, _seams
from tests.services.warming._support import (
    _Recorder,
    _seed_channel,
    _set_settings,
)


def test_roll_daily_resets_on_new_day() -> None:
    record = WarmingStateRecord(
        account_id="a",
        state="sleeping",
        updated_at="t",
        daily_actions=5,
        daily_count_date="2026-06-11",
    )
    assert warming._roll_daily(record, "2026-06-12") == (0, "2026-06-12")


def test_roll_daily_keeps_same_day() -> None:
    record = WarmingStateRecord(
        account_id="a",
        state="sleeping",
        updated_at="t",
        daily_actions=5,
        daily_count_date="2026-06-12",
    )
    assert warming._roll_daily(record, "2026-06-12") == (5, "2026-06-12")


def test_roll_daily_handles_missing_record() -> None:
    assert warming._roll_daily(None, "2026-06-12") == (0, "2026-06-12")


@pytest.mark.asyncio
async def test_run_loop_iteration_parks_when_daily_cap_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    # A fresh account is intro-capped by the auto cap (П2 retired the fleet-wide
    # override); enforce_readiness off so the daily gate is the one that fires,
    # not the П3 readiness gate.
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )
    await create_account(AccountCreate(account_id="acc-1"))
    today = datetime.now(UTC).date().isoformat()
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="sleeping",
            daily_actions=settings.warming.phase_daily_cap["intro"],
            daily_count_date=today,
        ),
    )

    result = await warming.run_loop_iteration("acc-1")

    assert result.status == "skipped"
    assert result.detail == "daily limit"
    assert recorder.actions == []
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.state == "sleeping"
    assert record.next_run_at is not None


@pytest.mark.asyncio
async def test_phase_cap_governs_daily_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The per-account auto cap (phase/trust) is the sole daily governor (audit П2;
    # the legacy fleet-wide override was removed). A fresh account is intro-capped
    # at the intro cap, so a daily_actions already at that cap parks the account.
    # enforce_readiness off so the daily gate is reached, not the П3 readiness gate.
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )
    await create_account(AccountCreate(account_id="acc-1"))
    today = datetime.now(UTC).date().isoformat()
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="sleeping",
            daily_actions=settings.warming.phase_daily_cap["intro"],
            daily_count_date=today,
        ),
    )

    result = await warming.run_loop_iteration("acc-1")

    assert result.status == "skipped"
    assert result.detail == "daily limit"
    assert recorder.actions == []


@pytest.mark.asyncio
async def test_run_loop_iteration_increments_daily_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")
    await create_account(AccountCreate(account_id="acc-1"))

    await warming.run_loop_iteration("acc-1")

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.daily_count_date == datetime.now(UTC).date().isoformat()
    # One channel per cycle: set_online + join + read + the story glance = 4
    # attempts (set_offline does not count). The story step is last before the DM
    # step, so it only fits now that the intro cap leaves room past the reads — at
    # the old cap of 3 the cycle was truncated right after the read.
    assert record.daily_actions == 4


@pytest.mark.asyncio
async def test_daily_limit_excludes_offline_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")

    # Give it only 2 remaining actions: SetOnline(True) uses 1, Join uses 1.
    # It should not attempt Read, but SetOnline(False) should still run.
    result = await warming.run_one_cycle(
        WarmingCycleRequest(account_id="acc-1", remaining_actions=2)
    )

    assert result.attempted_actions == 2
    types = recorder.types()
    assert types == ["set_online", "join_channel", "set_online"]
    assert result.channels_joined == 1
    assert result.channels_read == 0


class _CrashAfter:
    """Dispatcher that stops responding after N actions — models a killed process."""

    def __init__(self, limit: int) -> None:
        self.actions: list[str] = []
        self._limit = limit

    async def execute(self, account_id: str, action: TelegramAction) -> ActionResult:
        if len(self.actions) >= self._limit:
            msg = "process killed"
            raise RuntimeError(msg)
        self.actions.append(action.action_type)
        return ActionResult(
            status="ok",
            action_type=action.action_type,
            account_id=account_id,
            recent_message_ids=["101", "102"] if action.action_type == "read_channel" else None,
        )


@pytest.mark.asyncio
async def test_daily_budget_is_not_respent_after_a_crash_mid_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#208: a crash before the finalize write must not hand the loop a fresh budget."""
    monkeypatch.setattr(settings.warming, "quiet_day_weekday_probability", 0.0)
    monkeypatch.setattr(settings.warming, "quiet_day_weekend_probability", 0.0)
    crash = _CrashAfter(2)
    monkeypatch.setattr(_seams, "execute", crash.execute)
    await _seed_channel()
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="active"))

    with pytest.raises(RuntimeError):
        await warming.run_loop_iteration("acc-1")

    # The process died after SetOnline + the join, before ``_finalize_after_cycle``.
    assert crash.actions == ["set_online", "join_channel"]
    record = await fetch_warming_state("acc-1")
    assert record is not None
    # The row still carries the pre-cycle reservation, so today's budget is spent.
    assert record.daily_actions == settings.warming.phase_daily_cap["intro"]

    # Restart: the reconciled loop re-runs the iteration on the same calendar day.
    survivor = _Recorder()
    monkeypatch.setattr(_seams, "execute", survivor.execute)

    result = await warming.run_loop_iteration("acc-1")

    assert result.detail == "daily limit"
    assert survivor.actions == []


@pytest.mark.asyncio
async def test_daily_gate_allows_one_cycle_for_a_cap_of_one() -> None:
    """A tiny cap (e.g. a legacy override of 1) must still run once a day, not park forever."""
    await create_account(AccountCreate(account_id="acc-1"))
    today = datetime.now(UTC).date().isoformat()
    now = datetime.now(UTC)

    # cap=1, nothing done yet -> the gate lets the cycle proceed (returns None).
    assert await _loop._gate_daily_limit("acc-1", 1, (0, today), now, run_id=None) is None
    # cap=1, the one action already spent today -> park.
    parked = await _loop._gate_daily_limit("acc-1", 1, (1, today), now, run_id=None)
    assert parked is not None
    assert parked.detail == "daily limit"
