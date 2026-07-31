"""Post-cycle finalization contracts at stop/restart and partial-write boundaries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.db import create_account, fetch_warming_state, upsert_warming_state
from schemas.accounts import AccountCreate
from schemas.warming import WarmingCycleResult, WarmingStateRecord, WarmingStateWrite
from services.warming import _loop


def _result(status: str = "ok") -> WarmingCycleResult:
    return WarmingCycleResult.model_validate(
        {
            "account_id": "acc-1",
            "status": status,
            "attempted_actions": 4,
            "last_failed_action": "read" if status == "failed" else None,
            "last_failed_channel": "@one" if status == "failed" else None,
            "detail": "network" if status == "failed" else None,
        }
    )


def _record(state: str = "active", run_id: str | None = "gen-a") -> WarmingStateRecord:
    return WarmingStateRecord(
        account_id="acc-1",
        state=state,  # ty: ignore[invalid-argument-type]
        updated_at="2026-07-17T12:00:00+00:00",
        run_id=run_id,
        current_phase="intro",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "latest",
    [_record(run_id="gen-b"), _record(state="idle", run_id="gen-a")],
    ids=["new-generation", "stopped"],
)
async def test_finalize_does_not_resurrect_replaced_or_stopped_run(
    monkeypatch: pytest.MonkeyPatch, latest: WarmingStateRecord
) -> None:
    async def fetch(_account_id: str) -> WarmingStateRecord:
        return latest

    async def forbidden(*_args: object, **_kwargs: object) -> object:
        message = "terminal/stale row must not be finalized"
        raise AssertionError(message)

    monkeypatch.setattr(_loop, "fetch_warming_state", fetch)
    monkeypatch.setattr(_loop, "_resolve_phase_after_cycle", forbidden)
    monkeypatch.setattr(_loop, "_set_state", forbidden)
    result = _result()

    returned = await _loop._finalize_after_cycle(
        "acc-1",
        result,
        24.0,
        (7, "2026-07-17"),
        (4, datetime.now(UTC) + timedelta(hours=1), "sleeping"),
        run_id="gen-a",
    )

    assert returned is result


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "expected_cycles"), [("ok", 1), ("skipped", 0)])
async def test_finalize_persists_outcome_and_counts_only_real_cycle(
    monkeypatch: pytest.MonkeyPatch, status: str, expected_cycles: int
) -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="active",
            run_id="gen-a",
            current_phase="intro",
            phase_entered_at="2026-07-10T12:00:00+00:00",
            daily_actions=7,
            daily_count_date="2026-07-17",
        )
    )

    async def phase(*_args: object, **_kwargs: object) -> tuple[str, str, None]:
        return "intro", "2026-07-10T12:00:00+00:00", None

    monkeypatch.setattr(_loop, "_resolve_phase_after_cycle", phase)
    next_run = datetime.now(UTC) + timedelta(hours=2)
    result = _result(status)

    returned = await _loop._finalize_after_cycle(
        "acc-1",
        result,
        24.0,
        (7, "2026-07-17"),
        (4, next_run, "sleeping"),
        run_id="gen-a",
    )

    assert returned is result
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.state == "sleeping"
    assert record.daily_actions == 11
    assert record.daily_count_date == "2026-07-17"
    assert record.next_run_at == next_run.isoformat()
    assert record.cycles_completed == expected_cycles
    assert record.last_event == f"cycle:{status}"


@pytest.mark.asyncio
async def test_finalize_preserves_failure_context_for_operator() -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="acc-1", state="active", run_id="gen-a")
    )
    result = _result("failed")

    await _loop._finalize_after_cycle(
        "acc-1",
        result,
        72.0,
        (0, "2026-07-17"),
        (4, datetime.now(UTC) + timedelta(hours=2), "error"),
        run_id="gen-a",
    )

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.state == "error"
    assert record.last_action == "read"
    assert record.last_channel == "@one"
    assert record.last_error == "network"
