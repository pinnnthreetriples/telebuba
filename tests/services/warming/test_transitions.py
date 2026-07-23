"""Warming tests split from the former service test module: test_transitions.py."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.db import (
    fetch_warming_state,
    upsert_warming_state,
)
from schemas.warming import (
    WarmingStateRecord,
    WarmingStateWrite,
    WarmingStateWriteResult,
)
from services import warming
from services.warming import _seams, _transitions
from services.warming.pacing import (
    _seconds_until,
)
from tests.services.warming._support import (
    _Recorder,
    _seed_ready_account,
    _set_settings,
)


@pytest.mark.asyncio
async def test_loop_auto_completes_at_target_days(monkeypatch: pytest.MonkeyPatch) -> None:
    # Once warming has run for the operator-chosen target, the loop parks the
    # account complete (no further cycle) instead of warming on indefinitely.
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_ready_account("acc-1")
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)
    old_start = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1", state="sleeping", started_at=old_start, target_days=3
        ),
    )

    result = await warming.run_loop_iteration("acc-1")

    assert result.status == "skipped"
    assert result.detail == "target reached"
    assert recorder.actions == []  # no cycle ran
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.state == "sleeping"
    assert record.last_event == "warming_complete"


@pytest.mark.asyncio
async def test_loop_keeps_warming_before_target(monkeypatch: pytest.MonkeyPatch) -> None:
    # Below the chosen target the account keeps cycling normally.
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_ready_account("acc-1")
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)
    recent_start = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1", state="sleeping", started_at=recent_start, target_days=7
        ),
    )

    result = await warming.run_loop_iteration("acc-1")

    assert result.status == "ok"
    assert "set_online" in recorder.types()


@pytest.mark.asyncio
async def test_loop_target_complete_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    # A row already flagged complete re-parks silently — no second cycle, no re-log.
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_ready_account("acc-1")
    old_start = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="sleeping",
            started_at=old_start,
            target_days=3,
            last_event="warming_complete",
        ),
    )

    result = await warming.run_loop_iteration("acc-1")

    assert result.status == "skipped"
    assert result.detail == "target reached"
    assert recorder.actions == []


@pytest.mark.asyncio
async def test_target_gate_bails_on_stale_run(monkeypatch: pytest.MonkeyPatch) -> None:
    # A concurrent stop/restart flips run_id, so the CAS write is rejected and the
    # gate bails as "stale run" rather than logging a phantom completion.
    record = WarmingStateRecord(
        account_id="acc-1",
        state="sleeping",
        updated_at="2026-06-01T00:00:00+00:00",
        started_at=(datetime.now(UTC) - timedelta(days=10)).isoformat(),
        target_days=3,
    )

    async def _rejected(*_args: object, **_kwargs: object) -> WarmingStateWriteResult:
        return WarmingStateWriteResult(record=record, applied=False)

    monkeypatch.setattr(_transitions, "_set_state", _rejected)

    result = await _transitions._gate_target_reached(
        "acc-1", record, datetime.now(UTC), run_id="gen-1"
    )

    assert result is not None
    assert result.detail == "stale run"


@pytest.mark.asyncio
async def test_target_complete_reparks_future_next_run(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression: the idempotent target-reached branch must rewrite a fresh future
    # ``next_run_at``. Without it, once the first parked midnight passes the loop
    # busy-spins (``_loop_sleep_seconds`` clamps a past time to 0 and sleeps 0s).
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_ready_account("acc-1")
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)
    old_start = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    # Seed a row already flagged complete, with a next_run_at in the PAST (the
    # midnight the first pass parked has since elapsed) — the busy-spin trigger.
    stale_next = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="sleeping",
            started_at=old_start,
            target_days=3,
            last_event="warming_complete",
            next_run_at=stale_next,
        ),
    )

    result = await warming.run_loop_iteration("acc-1")

    assert result.status == "skipped"
    assert result.detail == "target reached"
    assert recorder.actions == []  # still no cycle work
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.next_run_at is not None
    # The re-parked schedule must be in the future, so the loop sleeps a positive
    # interval instead of tight-spinning with asyncio.sleep(0).
    assert _seconds_until(record.next_run_at, datetime.now(UTC)) > 0.0
