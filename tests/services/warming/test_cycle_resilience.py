"""Warming tests split from the former service test module: test_cycle_resilience.py."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

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
    WarmingCycleResult,
    WarmingStateWrite,
    WarmingStateWriteResult,
)
from services import warming
from services.warming import _loop, _seams
from tests.services.warming._support import (
    _Recorder,
    _seed_channel,
    _set_settings,
)


@pytest.mark.asyncio
async def test_lone_set_online_failure_sleeps_not_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient SetOnline failure must not park a healthy account in error (#100)."""

    async def execute(account_id: str, action: TelegramAction) -> ActionResult:
        status = "failed" if action.action_type == "set_online" else "ok"
        return ActionResult(status=status, action_type=action.action_type, account_id=account_id)

    monkeypatch.setattr(_seams, "execute", execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")
    await create_account(AccountCreate(account_id="acc-1"))

    await warming.run_loop_iteration("acc-1")

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.state == "sleeping"


@pytest.mark.asyncio
async def test_flood_wait_without_duration_parks_well_into_the_future(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown flood duration must cool down, not collapse to a 0s retry (#100)."""
    # Isolate the cool-down math from the active-hours shift (FIX #8 now routes
    # flood_wait through it too — covered separately below).
    monkeypatch.setattr(settings.warming, "active_hours_enabled", False)
    before = datetime.now(UTC)
    _, next_run_dt, next_state = await _loop._calculate_next_run(
        "acc-1",
        WarmingCycleResult(account_id="acc-1", status="flood_wait", flood_wait_seconds=None),
        "normal",
        40,
    )
    assert next_state == "flood_wait"
    floor = settings.warming.flood_wait_fallback_hours * 3600 - 5
    assert (next_run_dt - before).total_seconds() >= floor

    # A concrete duration (even tiny) is still honoured as Telegram instructed.
    _, soon_dt, _ = await _loop._calculate_next_run(
        "acc-1",
        WarmingCycleResult(account_id="acc-1", status="flood_wait", flood_wait_seconds=5),
        "normal",
        40,
    )
    assert (soon_dt - datetime.now(UTC)).total_seconds() < 60


@pytest.mark.asyncio
async def test_flood_wait_applies_human_margin(monkeypatch: pytest.MonkeyPatch) -> None:
    """FIX #8: a timed FloodWait resumes strictly after the raw duration (human margin)."""
    monkeypatch.setattr(settings.warming, "active_hours_enabled", False)  # isolate the margin
    monkeypatch.setattr(settings.warming, "flood_wait_margin_fraction", 0.2)
    monkeypatch.setattr(_seams.rng, "uniform", lambda _a, b: b)  # deterministic max margin
    before = datetime.now(UTC)
    _, next_run_dt, next_state = await _loop._calculate_next_run(
        "acc-1",
        WarmingCycleResult(account_id="acc-1", status="flood_wait", flood_wait_seconds=100),
        "normal",
        40,
    )
    assert next_state == "flood_wait"
    # 100s inflated by the 1 + 0.2 margin → strictly beyond the raw wait.
    assert (next_run_dt - before).total_seconds() > 100


@pytest.mark.asyncio
async def test_flood_wait_expiry_defers_into_active_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """FIX #8: a flood_wait expiring at night is deferred into the morning window."""
    monkeypatch.setattr(settings.warming, "active_hours_enabled", True)
    monkeypatch.setattr(settings.warming, "active_hours_start", 8)
    monkeypatch.setattr(settings.warming, "active_hours_end", 23)
    monkeypatch.setattr(settings.warming, "active_hours_start_spread_minutes", 0)  # exact snap
    monkeypatch.setattr(settings.warming, "flood_wait_margin_fraction", 0.0)  # exact duration
    before = datetime.now(UTC)
    # Duration chosen so the wait expires at 03:00 UTC (night). No account row →
    # _account_tz resolves to None, so the window is interpreted in UTC.
    target_night = (before + timedelta(days=1)).replace(hour=3, minute=0, second=0, microsecond=0)
    flood_seconds = int((target_night - before).total_seconds())
    _, next_run_dt, next_state = await _loop._calculate_next_run(
        "acc-flood",
        WarmingCycleResult(
            account_id="acc-flood", status="flood_wait", flood_wait_seconds=flood_seconds
        ),
        "normal",
        40,
    )
    assert next_state == "flood_wait"
    # 03:00 night → deferred to 08:00 (the active-window start), still in the future.
    assert next_run_dt.astimezone(UTC).hour == 8
    assert next_run_dt > before


@pytest.mark.asyncio
async def test_cycle_cancellation_still_sets_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    # FIX #11a: cancelling a cycle mid-flight must propagate CancelledError AND
    # still run the finally: _set_offline cleanup (SetOnline(False)) so a killed
    # account is not left showing "online" forever.
    inside_join = asyncio.Event()
    offline_sent = asyncio.Event()

    async def blocking_execute(account_id: str, action: TelegramAction) -> ActionResult:
        if action.action_type == "join_channel":
            inside_join.set()
            await asyncio.Event().wait()  # block forever → the test cancels here
        if action.action_type == "set_online" and getattr(action, "online", None) is False:
            offline_sent.set()
        return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)

    monkeypatch.setattr(_seams, "execute", blocking_execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")
    await create_account(AccountCreate(account_id="acc-1"))

    task = asyncio.create_task(warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1")))
    await asyncio.wait_for(inside_join.wait(), timeout=1.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert offline_sent.is_set()  # the finally cleanup dispatched SetOnline(False)


@pytest.mark.asyncio
async def test_no_reaction_after_failed_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed read must not trigger a reaction on the same channel (#100)."""
    seen: list[str] = []

    async def execute(account_id: str, action: TelegramAction) -> ActionResult:
        seen.append(action.action_type)
        status = "failed" if action.action_type == "read_channel" else "ok"
        return ActionResult(status=status, action_type=action.action_type, account_id=account_id)

    monkeypatch.setattr(_seams, "execute", execute)
    monkeypatch.setattr(settings.warming, "reaction_probability", 1.0)
    await _seed_channel()
    await _set_settings(chat=False, reactions=True, key="")

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert "react_to_post" not in seen
    assert result.reactions_sent == 0


@pytest.mark.asyncio
async def test_cycle_skipped_when_only_set_online_fits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One slot below the cap → park, don't burn a sleep on a presence-only cycle (#100)."""
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    # Fresh account → intro auto cap of 3 (П2 retired the fleet override);
    # enforce_readiness off so the daily gate fires, not the П3 readiness gate.
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
            daily_actions=2,  # one below the cap of 3 — only SetOnline would fit
            daily_count_date=today,
        ),
    )

    result = await warming.run_loop_iteration("acc-1")

    assert result.status == "skipped"
    assert result.detail == "daily limit"
    assert recorder.actions == []  # no presence-only cycle ran


@pytest.mark.asyncio
async def test_phase_advanced_not_logged_when_finalize_cas_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No phantom phase_advanced when the final CAS write is rejected (#100)."""
    await create_account(AccountCreate(account_id="acc-1"))
    today = datetime.now(UTC).date().isoformat()
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="active",
            run_id="run-a",
            current_phase="intro",
        ),
    )
    events: list[str] = []

    async def fake_log(_level: str, event: str, **_kwargs: object) -> None:
        events.append(event)

    monkeypatch.setattr(_loop, "log_event", fake_log)

    async def rejecting_set_state(
        account_id: str,
        _state: object = None,
        **_kwargs: object,
    ) -> WarmingStateWriteResult:
        # Simulate the CAS rejecting the final write (a newer generation took the
        # row): return applied=False without mutating state.
        record = await fetch_warming_state(account_id)
        assert record is not None
        return WarmingStateWriteResult(record=record, applied=False)

    monkeypatch.setattr(_loop, "_set_state", rejecting_set_state)

    await _loop._finalize_after_cycle(
        "acc-1",
        WarmingCycleResult(account_id="acc-1", status="ok"),
        365 * 24.0,  # huge age → phase would jump from the stale "intro"
        (0, today),
        (0, datetime.now(UTC), "sleeping"),
        run_id="run-a",
    )

    assert "phase_advanced" not in events


@pytest.mark.asyncio
async def test_phase_advanced_logged_when_finalize_applies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The transition is still announced when the write actually lands (#100)."""
    await create_account(AccountCreate(account_id="acc-1"))
    today = datetime.now(UTC).date().isoformat()
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="active",
            run_id="run-a",
            current_phase="intro",
        ),
    )
    events: list[str] = []

    async def fake_log(_level: str, event: str, **_kwargs: object) -> None:
        events.append(event)

    monkeypatch.setattr(_loop, "log_event", fake_log)

    await _loop._finalize_after_cycle(
        "acc-1",
        WarmingCycleResult(account_id="acc-1", status="ok"),
        365 * 24.0,
        (0, today),
        (0, datetime.now(UTC), "sleeping"),
        run_id="run-a",
    )

    assert "phase_advanced" in events
