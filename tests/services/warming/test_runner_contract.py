"""Long-running wrapper contracts without wall-clock sleeps."""

from __future__ import annotations

import asyncio

import pytest

from schemas.warming import WarmingStateRecord, WarmingStateWriteResult
from services.warming import _runner


def _record(state: str, run_id: str | None = "gen-a") -> WarmingStateRecord:
    return WarmingStateRecord.model_validate(
        {
            "account_id": "acc-1",
            "state": state,
            "updated_at": "2026-07-17T12:00:00+00:00",
            "run_id": run_id,
        }
    )


@pytest.mark.parametrize(
    ("record", "run_id", "expected"),
    [
        (None, None, False),
        (_record("idle"), None, False),
        (_record("error"), "gen-a", False),
        (_record("active", None), None, True),
        (_record("sleeping"), "gen-a", True),
        (_record("flood_wait"), "gen-b", False),
        (_record("quarantine", "gen-a"), "gen-a", True),
    ],
)
def test_live_generation_matrix(
    record: WarmingStateRecord | None, run_id: str | None, expected: object
) -> None:
    assert _runner._is_live_generation(record, run_id) is expected


@pytest.mark.asyncio
async def test_replaced_generation_exits_before_schedule_or_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fetch(_account_id: str) -> WarmingStateRecord:
        calls.append("fetch")
        return _record("active", "gen-b")

    async def forbidden(*_args: object, **_kwargs: object) -> object:
        message = "stale generation must have no side effects"
        raise AssertionError(message)

    monkeypatch.setattr(_runner, "fetch_warming_state", fetch)
    monkeypatch.setattr(_runner, "_initial_delay_seconds", forbidden)
    monkeypatch.setattr(_runner, "run_loop_iteration", forbidden)

    await _runner._warming_loop("acc-1", run_id="gen-a")

    assert calls == ["fetch"]


@pytest.mark.asyncio
async def test_crash_is_persisted_only_for_still_live_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = iter([_record("active", "gen-a"), _record("active", "gen-a")])

    async def fetch(_account_id: str) -> WarmingStateRecord:
        return next(records)

    async def crash(*_args: object, **_kwargs: object) -> float:
        message = "schedule failed"
        raise RuntimeError(message)

    writes: list[dict[str, object]] = []

    async def set_state(_account_id: str, state: str, **kwargs: object) -> WarmingStateWriteResult:
        writes.append({"state": state, **kwargs})
        return WarmingStateWriteResult(record=_record("error", "gen-a"), applied=True)

    events: list[tuple[str, dict[str, object]]] = []

    async def log(_level: str, event: str, **kwargs: object) -> None:
        events.append((event, kwargs))

    monkeypatch.setattr(_runner, "fetch_warming_state", fetch)
    monkeypatch.setattr(_runner, "_initial_delay_seconds", crash)
    monkeypatch.setattr(_runner, "_set_state", set_state)
    monkeypatch.setattr(_runner, "log_event", log)

    await _runner._warming_loop("acc-1", run_id="gen-a")

    assert events[0][0] == "warming_loop_crashed"
    assert events[0][1]["extra"] == {
        "error_type": "RuntimeError",
        "message": "schedule failed",
    }
    assert writes == [
        {
            "state": "error",
            "last_event": "loop_crashed",
            "last_error": "RuntimeError: schedule failed",
            "heartbeat_at": writes[0]["heartbeat_at"],
            "expected_run_id": "gen-a",
        }
    ]


@pytest.mark.asyncio
async def test_crash_after_restart_logs_diagnostic_but_cannot_overwrite_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = iter([_record("active", "gen-a"), _record("active", "gen-b")])

    async def fetch(_account_id: str) -> WarmingStateRecord:
        return next(records)

    async def crash(*_args: object, **_kwargs: object) -> float:
        message = "boom"
        raise RuntimeError(message)

    writes = 0

    async def set_state(*_args: object, **_kwargs: object) -> object:
        nonlocal writes
        writes += 1
        message = "new generation owns the row"
        raise AssertionError(message)

    monkeypatch.setattr(_runner, "fetch_warming_state", fetch)
    monkeypatch.setattr(_runner, "_initial_delay_seconds", crash)
    monkeypatch.setattr(_runner, "_set_state", set_state)

    await _runner._warming_loop("acc-1", run_id="gen-a")

    assert writes == 0


@pytest.mark.asyncio
async def test_cancellation_propagates_without_crash_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fetch(_account_id: str) -> WarmingStateRecord:
        return _record("active")

    async def initial(*_args: object, **_kwargs: object) -> float:
        return 3600.0

    writes: list[object] = []

    async def set_state(*args: object, **_kwargs: object) -> None:
        writes.append(args)

    monkeypatch.setattr(_runner, "fetch_warming_state", fetch)
    monkeypatch.setattr(_runner, "_initial_delay_seconds", initial)
    monkeypatch.setattr(_runner, "_persist_cold_start_schedule", set_state)
    task = asyncio.create_task(_runner._warming_loop("acc-1", run_id="gen-a"))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(writes) == 1
