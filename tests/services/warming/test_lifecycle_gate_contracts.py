"""Public contracts for lifecycle gates that stop a warming iteration."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.db import (
    create_account,
    fetch_warming_state,
    save_warming_settings,
    upsert_spam_status,
    upsert_warming_state,
)
from core.repositories.logs import list_recent_logs
from schemas.accounts import AccountCreate
from schemas.spam_status import SpamStatusVerdict
from schemas.warming import WarmingStateWrite
from services import warming
from services.warming import _seams
from tests.services.warming._support import _Recorder, _seed_ready_account

if TYPE_CHECKING:
    from schemas.logs import LogEntry


def _events_named(entries: list[LogEntry], event: str) -> list[LogEntry]:
    return [entry for entry in entries if entry.event == event]


def _assert_utc_midnight(value: datetime) -> None:
    assert value.utcoffset() == timedelta(0)
    assert (value.hour, value.minute, value.second, value.microsecond) == (0, 0, 0, 0)


@pytest.mark.asyncio
async def test_degraded_active_account_is_parked_with_complete_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A readiness failure is externally visible and performs no Telegram work."""
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_ready_account("acc-1")
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=True,
        gemini_api_key="",
    )
    await upsert_spam_status(
        SpamStatusVerdict(
            account_id="acc-1",
            status="limited",
            detail="restricted until reviewed",
            checked_at="2026-07-17T12:00:00+00:00",
        )
    )
    stale_heartbeat = "2026-07-01T00:00:00+00:00"
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="active",
            run_id="generation-a",
            last_event="cycle_started",
            last_error=None,
            heartbeat_at=stale_heartbeat,
        )
    )
    started = datetime.now(UTC)

    result = await warming.run_loop_iteration("acc-1", run_id="generation-a")

    finished = datetime.now(UTC)
    assert result.model_dump(include={"account_id", "status", "detail"}) == {
        "account_id": "acc-1",
        "status": "error",
        "detail": "spam limited; trust critical",
    }
    assert recorder.actions == []

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.state == "error"
    assert record.run_id == "generation-a"
    assert record.last_event == "cycle_not_ready"
    assert record.last_error == "spam limited; trust critical"
    assert record.heartbeat_at is not None
    heartbeat = datetime.fromisoformat(record.heartbeat_at)
    assert started <= heartbeat <= finished
    assert record.heartbeat_at != stale_heartbeat

    events = _events_named(await list_recent_logs(limit=100), "warming_cycle_not_ready")
    assert len(events) == 1
    event = events[0]
    assert event.level == "WARNING"
    assert event.account_id == "acc-1"
    assert event.extra == {"reasons": ["spam limited", "trust critical"]}


@pytest.mark.asyncio
async def test_target_completion_is_observable_at_the_exact_day_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reaching the duration parks the account and emits one complete event."""
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await create_account(AccountCreate(account_id="acc-1"))
    started_at = (datetime.now(UTC) - timedelta(days=3, seconds=10)).isoformat()
    stale_heartbeat = "2026-07-01T00:00:00+00:00"
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="sleeping",
            run_id="generation-a",
            started_at=started_at,
            target_days=3,
            last_event="cycle_completed",
            next_run_at="2026-07-01T01:00:00+00:00",
            heartbeat_at=stale_heartbeat,
        )
    )
    first_started = datetime.now(UTC)

    first = await warming.run_loop_iteration("acc-1", run_id="generation-a")

    first_finished = datetime.now(UTC)
    assert first.model_dump(include={"account_id", "status", "detail"}) == {
        "account_id": "acc-1",
        "status": "skipped",
        "detail": "target reached",
    }
    assert recorder.actions == []
    completed = await fetch_warming_state("acc-1")
    assert completed is not None
    assert completed.state == "sleeping"
    assert completed.run_id == "generation-a"
    assert completed.started_at == started_at
    assert completed.target_days == 3
    assert completed.last_event == "warming_complete"
    assert completed.next_run_at is not None
    first_wake = datetime.fromisoformat(completed.next_run_at)
    _assert_utc_midnight(first_wake)
    assert completed.heartbeat_at is not None
    first_heartbeat = datetime.fromisoformat(completed.heartbeat_at)
    assert first_started <= first_heartbeat <= first_finished
    expected_first_wake = (first_heartbeat + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    assert first_wake == expected_first_wake
    first_events = _events_named(await list_recent_logs(limit=100), "warming_target_reached")
    assert len(first_events) == 1
    first_event = first_events[0]
    assert first_event.level == "INFO"
    assert first_event.account_id == "acc-1"
    assert first_event.extra == {"target_days": 3, "warming_days": 3}


@pytest.mark.asyncio
async def test_completed_target_is_reparked_without_duplicate_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A repeated iteration refreshes a stale wake time without re-announcing success."""
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await create_account(AccountCreate(account_id="acc-1"))
    started_at = (datetime.now(UTC) - timedelta(days=3, seconds=10)).isoformat()
    stale_heartbeat = "2026-07-01T00:00:00+00:00"

    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="sleeping",
            run_id="generation-a",
            started_at=started_at,
            target_days=3,
            last_event="warming_complete",
            next_run_at="2026-07-01T01:00:00+00:00",
            heartbeat_at=stale_heartbeat,
        )
    )
    second_started = datetime.now(UTC)

    result = await warming.run_loop_iteration("acc-1", run_id="generation-a")

    second_finished = datetime.now(UTC)
    assert result.model_dump(include={"account_id", "status", "detail"}) == {
        "account_id": "acc-1",
        "status": "skipped",
        "detail": "target reached",
    }
    assert recorder.actions == []
    repeated = await fetch_warming_state("acc-1")
    assert repeated is not None
    assert repeated.state == "sleeping"
    assert repeated.run_id == "generation-a"
    assert repeated.last_event == "warming_complete"
    assert repeated.next_run_at is not None
    second_wake = datetime.fromisoformat(repeated.next_run_at)
    _assert_utc_midnight(second_wake)
    assert repeated.heartbeat_at is not None
    second_heartbeat = datetime.fromisoformat(repeated.heartbeat_at)
    assert second_started <= second_heartbeat <= second_finished
    assert repeated.heartbeat_at != stale_heartbeat
    expected_second_wake = (second_heartbeat + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    assert second_wake == expected_second_wake
    repeated_events = _events_named(await list_recent_logs(limit=100), "warming_target_reached")
    assert repeated_events == []
