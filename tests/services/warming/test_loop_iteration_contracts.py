"""Loop-iteration policy propagation, partial failures, and stale gate outcomes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from core.db import (
    create_account,
    fetch_warming_state,
    load_warming_settings,
    save_warming_settings,
    upsert_warming_state,
)
from schemas.accounts import AccountCreate
from schemas.trust import TrustScore
from schemas.warming import (
    WarmingCycleRequest,
    WarmingCycleResult,
    WarmingIntensity,
    WarmingStateRecord,
    WarmingStateWrite,
    WarmingStateWriteResult,
)
from services.warming import _loop, _transitions
from tests.services.warming._support import _account, _seed_channel


def _record(**updates: object) -> WarmingStateRecord:
    base: dict[str, object] = {
        "account_id": "acc-1",
        "state": "active",
        "updated_at": "2026-07-17T12:00:00+00:00",
        "run_id": "generation-a",
        "daily_actions": 8,
        "daily_count_date": datetime.now(UTC).date().isoformat(),
        "activity_persona": "active",
    }
    base.update(updates)
    return WarmingStateRecord.model_validate(base)


def _write(record: WarmingStateRecord, *, applied: bool = True) -> WarmingStateWriteResult:
    return WarmingStateWriteResult(record=record, applied=applied)


@pytest.mark.asyncio
async def test_iteration_propagates_budget_trust_and_persona_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _record()
    trust = TrustScore(account_id="acc-1", score=85, band="good")
    intensity = WarmingIntensity(
        channels_min=1,
        channels_max=2,
        reaction_probability=0.3,
        dm_allowed=True,
        daily_cap=12,
        phase="settling",
    )
    writes: list[dict[str, object]] = []
    requests: list[WarmingCycleRequest] = []
    schedules: list[tuple[str, WarmingCycleResult, str, int]] = []
    finalizations: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def fetch_state(_account_id: str) -> WarmingStateRecord:
        return record

    async def fetch_account(_account_id: str):  # type: ignore[no-untyped-def]
        return _account()

    async def trust_score(_account_id: str) -> TrustScore:
        return trust

    async def no_gate(*_args: object, **_kwargs: object) -> None:
        return None

    async def set_state(_account_id: str, state: str, **kwargs: object) -> WarmingStateWriteResult:
        writes.append({"state": state, **kwargs})
        return _write(record)

    async def cycle(data: WarmingCycleRequest, **_kwargs: object) -> WarmingCycleResult:
        requests.append(data)
        return WarmingCycleResult(account_id=data.account_id, status="ok", attempted_actions=3)

    async def schedule(
        account_id: str,
        result: WarmingCycleResult,
        persona: str,
        daily_cap: int,
    ) -> tuple[int, datetime, str]:
        schedules.append((account_id, result, persona, daily_cap))
        return 3, datetime.now(UTC) + timedelta(hours=1), "sleeping"

    async def finalize(*args: object, **kwargs: object) -> WarmingCycleResult:
        finalizations.append((args, kwargs))
        return cast("WarmingCycleResult", args[1])

    monkeypatch.setattr(_loop, "fetch_warming_state", fetch_state)
    monkeypatch.setattr(_loop, "fetch_account", fetch_account)
    monkeypatch.setattr(_loop, "account_trust_score", trust_score)
    monkeypatch.setattr(_loop, "_gate_target_reached", no_gate)
    monkeypatch.setattr(_loop, "_gate_readiness", no_gate)
    monkeypatch.setattr(_loop, "_gate_quiet_day", no_gate)
    monkeypatch.setattr(_loop, "_gate_daily_limit", no_gate)
    monkeypatch.setattr(_loop, "compute_intensity", lambda *_args, **_kwargs: intensity)
    monkeypatch.setattr(_loop, "_set_state", set_state)
    monkeypatch.setattr(_loop, "run_one_cycle", cycle)
    monkeypatch.setattr(_loop, "_calculate_next_run", schedule)
    monkeypatch.setattr(_loop, "_finalize_after_cycle", finalize)

    result = await _loop.run_loop_iteration("acc-1", run_id="generation-a")

    assert result.status == "ok"
    assert len(requests) == 1
    request = requests[0]
    assert request.remaining_actions == 4
    assert request.dm_allowed is True
    assert request.activity_persona == "active"
    assert writes[0]["state"] == "active"
    assert writes[0]["last_event"] == "cycle_started"
    assert writes[0]["daily_actions"] == 8
    assert writes[0]["daily_count_date"] == record.daily_count_date
    assert len(schedules) == 1
    scheduled = schedules[0]
    assert scheduled[2:] == ("active", 12)
    assert len(finalizations) == 1
    finalize_args, finalize_kwargs = finalizations[0]
    assert finalize_args[3] == (8, record.daily_count_date)
    assert finalize_kwargs == {"run_id": "generation-a"}


@pytest.mark.asyncio
async def test_progress_events_are_monotonic_and_duplicate_safe(  # noqa: C901
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _record(daily_actions=0)
    progress: list[str | None] = []

    async def fetch_state(_account_id: str) -> WarmingStateRecord:
        return record

    async def no_gate(*_args: object, **_kwargs: object) -> None:
        return None

    async def set_state(_account_id: str, state: str, **kwargs: object) -> WarmingStateWriteResult:
        if state == "active":
            progress.append(cast("str | None", kwargs.get("last_action")))
        return _write(record)

    async def cycle(
        data: WarmingCycleRequest,
        *,
        on_step=None,  # type: ignore[no-untyped-def]
    ) -> WarmingCycleResult:
        assert on_step is not None
        for step in ("read", "join", "read", "unknown", "stories", "react", "send_dm"):
            await on_step(step)
        return WarmingCycleResult(account_id=data.account_id, status="ok")

    async def finalize(
        _account_id: str, result: WarmingCycleResult, *_args: object, **_kwargs: object
    ) -> WarmingCycleResult:
        return result

    async def trust_score(_account_id: str) -> TrustScore:
        return TrustScore(account_id="acc-1", score=80, band="good")

    async def fetch_account(_account_id: str):  # type: ignore[no-untyped-def]
        return _account()

    async def schedule(*_args: object, **_kwargs: object) -> tuple[int, datetime, str]:
        return 0, datetime.now(UTC), "sleeping"

    monkeypatch.setattr(_loop, "fetch_warming_state", fetch_state)
    monkeypatch.setattr(_loop, "fetch_account", fetch_account)
    monkeypatch.setattr(_loop, "account_trust_score", trust_score)
    monkeypatch.setattr(_loop, "_gate_target_reached", no_gate)
    monkeypatch.setattr(_loop, "_gate_readiness", no_gate)
    monkeypatch.setattr(_loop, "_gate_quiet_day", no_gate)
    monkeypatch.setattr(_loop, "_gate_daily_limit", no_gate)
    monkeypatch.setattr(_loop, "_set_state", set_state)
    monkeypatch.setattr(_loop, "run_one_cycle", cycle)
    monkeypatch.setattr(
        _loop,
        "_calculate_next_run",
        schedule,
    )
    monkeypatch.setattr(_loop, "_finalize_after_cycle", finalize)

    await _loop.run_loop_iteration("acc-1", run_id="generation-a")

    # cycle_started seeds set_online; later callbacks can only move forward.
    assert progress == ["set_online", "read", "stories", "send_dm"]


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["quiet", "daily"])
async def test_parking_gate_losing_cas_reports_stale_without_cycle(
    monkeypatch: pytest.MonkeyPatch, gate: str
) -> None:
    record = _record()
    calls: list[dict[str, object]] = []

    async def rejected(_account_id: str, state: str, **kwargs: object) -> WarmingStateWriteResult:
        calls.append({"state": state, **kwargs})
        return _write(record, applied=False)

    async def tz(_account_id: str) -> None:
        return None

    monkeypatch.setattr(_loop, "_set_state", rejected)
    monkeypatch.setattr(_loop, "_account_tz", tz)
    monkeypatch.setattr(_loop, "_is_quiet_day", lambda *_args: gate == "quiet")
    now = datetime(2026, 7, 17, 12, tzinfo=UTC)

    if gate == "quiet":
        result = await _loop._gate_quiet_day("acc-1", (3, "2026-07-17"), now, run_id="generation-a")
    else:
        result = await _loop._gate_daily_limit(
            "acc-1", 5, (5, "2026-07-17"), now, run_id="generation-a"
        )

    assert result is not None
    assert result.detail == "stale run"
    assert calls[0]["expected_run_id"] == "generation-a"
    assert calls[0]["state"] == "sleeping"


@pytest.mark.asyncio
async def test_readiness_gate_losing_cas_emits_no_phantom_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=True,
        gemini_api_key="",
    )
    controls = await load_warming_settings()
    record = _record(state="sleeping")
    events: list[str] = []

    async def rejected(*_args: object, **_kwargs: object) -> WarmingStateWriteResult:
        return _write(record, applied=False)

    async def log(_level: str, event: str, **_kwargs: object) -> None:
        events.append(event)

    monkeypatch.setattr(_transitions, "_set_state", rejected)
    monkeypatch.setattr(_transitions, "log_event", log)

    result = await _loop._gate_readiness(
        _account(status="new"),
        controls,
        record,
        TrustScore(account_id="acc-1", score=20, band="critical"),
        datetime(2026, 7, 17, 12, tzinfo=UTC),
        run_id="generation-a",
    )

    assert result is not None
    assert result.status == "skipped"
    assert result.detail == "stale run"
    assert events == []


@pytest.mark.asyncio
async def test_cycle_exception_leaves_truthful_active_partial_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    await _seed_channel()
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="sleeping"))

    async def fail_cycle(*_args: object, **_kwargs: object) -> WarmingCycleResult:
        message = "Telegram disconnected"
        raise RuntimeError(message)

    monkeypatch.setattr(_loop, "run_one_cycle", fail_cycle)

    with pytest.raises(RuntimeError, match="Telegram disconnected"):
        await _loop.run_loop_iteration("acc-1")

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.state == "active"
    assert record.last_event == "cycle_started"
    assert record.last_action == "set_online"
    assert record.last_cycle_at is None


@pytest.mark.asyncio
async def test_missing_row_with_generation_is_rejected_before_other_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_state(_account_id: str) -> None:
        return None

    async def forbidden(*_args: object, **_kwargs: object) -> object:
        message = "stale generation must not fetch account or trust"
        raise AssertionError(message)

    monkeypatch.setattr(_loop, "fetch_warming_state", no_state)
    monkeypatch.setattr(_loop, "fetch_account", forbidden)
    monkeypatch.setattr(_loop, "account_trust_score", forbidden)

    result = await _loop.run_loop_iteration("acc-1", run_id="generation-a")

    assert result.status == "skipped"
    assert result.detail == "stale run"
