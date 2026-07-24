"""Persistence contract for every operator-visible warming state field."""

from __future__ import annotations

import pytest

from core.db import create_account, fetch_warming_state
from schemas.accounts import AccountCreate
from services.warming._state import _set_state


@pytest.mark.asyncio
async def test_state_write_roundtrips_complete_cycle_diagnostics() -> None:
    await create_account(AccountCreate(account_id="acc-1"))

    write = await _set_state(
        "acc-1",
        "flood_wait",
        last_event="cycle:flood_wait",
        last_cycle_at="2026-07-17T12:00:00+00:00",
        next_run_at="2026-07-17T13:00:00+00:00",
        increment_cycle=True,
        last_error="rate limited",
        last_action="read_channel",
        last_channel="@one",
        heartbeat_at="2026-07-17T12:00:01+00:00",
        started_at="2026-07-10T12:00:00+00:00",
        stopped_at="2026-07-09T12:00:00+00:00",
        flood_wait_seconds=3600,
        flood_wait_until="2026-07-17T13:00:00+00:00",
        proxy_snapshot="socks5://127.0.0.1:1080 (US)",
        daily_actions=9,
        daily_count_date="2026-07-17",
        quarantine_count=2,
        run_id="generation-a",
        current_phase="warming",
        phase_entered_at="2026-07-15T12:00:00+00:00",
        target_days=21,
        activity_persona="active",
    )

    assert write.applied is True
    record = write.record
    assert record.state == "flood_wait"
    assert record.cycles_completed == 1
    assert record.last_event == "cycle:flood_wait"
    assert record.last_cycle_at == "2026-07-17T12:00:00+00:00"
    assert record.next_run_at == "2026-07-17T13:00:00+00:00"
    assert record.last_error == "rate limited"
    assert record.last_action == "read_channel"
    assert record.last_channel == "@one"
    assert record.heartbeat_at == "2026-07-17T12:00:01+00:00"
    assert record.started_at == "2026-07-10T12:00:00+00:00"
    assert record.stopped_at == "2026-07-09T12:00:00+00:00"
    assert record.flood_wait_seconds == 3600
    assert record.flood_wait_until == "2026-07-17T13:00:00+00:00"
    assert record.proxy_snapshot == "socks5://127.0.0.1:1080 (US)"
    assert record.daily_actions == 9
    assert record.daily_count_date == "2026-07-17"
    assert record.quarantine_count == 2
    assert record.run_id == "generation-a"
    assert record.current_phase == "warming"
    assert record.phase_entered_at == "2026-07-15T12:00:00+00:00"
    assert record.target_days == 21
    assert record.activity_persona == "active"


@pytest.mark.asyncio
async def test_state_transition_carries_every_unspecified_field() -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    seeded = await _set_state(
        "acc-1",
        "active",
        last_event="cycle_started",
        last_cycle_at="2026-07-17T12:00:00+00:00",
        next_run_at="2026-07-17T13:00:00+00:00",
        last_error="old error",
        last_action="react",
        last_channel="@one",
        heartbeat_at="2026-07-17T12:00:01+00:00",
        started_at="2026-07-10T12:00:00+00:00",
        stopped_at="2026-07-09T12:00:00+00:00",
        flood_wait_seconds=60,
        flood_wait_until="2026-07-17T12:01:00+00:00",
        proxy_snapshot="proxy-a",
        daily_actions=4,
        daily_count_date="2026-07-17",
        quarantine_count=3,
        run_id="generation-a",
        current_phase="settling",
        phase_entered_at="2026-07-12T00:00:00+00:00",
        target_days=14,
        activity_persona="calm",
    )

    transitioned = await _set_state("acc-1", "sleeping", last_event="parked")

    before = seeded.record.model_dump(exclude={"state", "updated_at", "last_event"})
    after = transitioned.record.model_dump(exclude={"state", "updated_at", "last_event"})
    assert after == before
    assert transitioned.record.state == "sleeping"
    assert transitioned.record.last_event == "parked"


@pytest.mark.asyncio
async def test_explicit_clear_removes_stale_runtime_diagnostics() -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    await _set_state(
        "acc-1",
        "flood_wait",
        next_run_at="2026-07-17T13:00:00+00:00",
        last_error="limited",
        last_action="send_dm",
        last_channel="@one",
        heartbeat_at="2026-07-17T12:00:00+00:00",
        started_at="2026-07-10T12:00:00+00:00",
        stopped_at="2026-07-09T12:00:00+00:00",
        flood_wait_seconds=60,
        flood_wait_until="2026-07-17T12:01:00+00:00",
        proxy_snapshot="proxy-a",
        daily_count_date="2026-07-17",
        run_id="generation-a",
        current_phase="settling",
        phase_entered_at="2026-07-12T00:00:00+00:00",
        target_days=14,
        activity_persona="calm",
    )

    cleared = await _set_state(
        "acc-1",
        "idle",
        next_run_at=None,
        last_error=None,
        last_action=None,
        last_channel=None,
        heartbeat_at=None,
        started_at=None,
        stopped_at=None,
        flood_wait_seconds=None,
        flood_wait_until=None,
        proxy_snapshot=None,
        daily_actions=0,
        daily_count_date=None,
        quarantine_count=0,
        run_id=None,
        current_phase=None,
        phase_entered_at=None,
        target_days=None,
        activity_persona=None,
    )

    record = cleared.record
    assert record.state == "idle"
    for field in (
        "next_run_at",
        "last_error",
        "last_action",
        "last_channel",
        "heartbeat_at",
        "started_at",
        "stopped_at",
        "flood_wait_seconds",
        "flood_wait_until",
        "proxy_snapshot",
        "daily_count_date",
        "run_id",
        "current_phase",
        "phase_entered_at",
        "target_days",
    ):
        assert getattr(record, field) is None
    assert record.daily_actions == 0
    assert record.quarantine_count == 0
    # Explicit None resets the persisted persona to the repository default;
    # omitting the argument is the distinct "carry current" operation.
    assert record.activity_persona == "normal"
    assert await fetch_warming_state("acc-1") == record
