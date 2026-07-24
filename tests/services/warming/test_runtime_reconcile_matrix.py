"""Restart reconciliation across every persisted warming state."""

from __future__ import annotations

import asyncio

import pytest

from core.db import create_account, fetch_warming_state, save_warming_settings, upsert_warming_state
from schemas.accounts import AccountCreate
from schemas.warming import WarmingStateWrite
from services import warming
from services.warming import _runtime


async def _cancel_runtime_tasks() -> None:
    tasks = list(warming._RUNTIME.values())
    warming._RUNTIME.clear()
    for task in tasks:
        if not task.done():
            task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_reconcile_state_matrix_and_generation_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[tuple[str, str | None]] = []

    async def loop(account_id: str, *, run_id: str | None = None) -> None:
        started.append((account_id, run_id))
        await asyncio.Event().wait()

    monkeypatch.setattr(_runtime, "_warming_loop", loop)
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )
    states = {
        "active": "active",
        "sleep": "sleeping",
        "wait": "flood_wait",
        "quarantine": "quarantine",
        "idle": "idle",
        "error": "error",
    }
    for account_id, state in states.items():
        await create_account(AccountCreate(account_id=account_id))
        await upsert_warming_state(
            WarmingStateWrite(
                account_id=account_id,
                state=state,  # ty: ignore[invalid-argument-type]
                run_id="old-generation",
            )
        )

    try:
        await warming.reconcile_warming_runtime()
        await asyncio.sleep(0)

        expected = {"active", "sleep", "wait", "quarantine"}
        assert set(warming._RUNTIME) == expected
        assert {account_id for account_id, _run_id in started} == expected
        run_ids = dict(started)
        assert all(run_ids[account_id] not in (None, "old-generation") for account_id in expected)
        assert len(set(run_ids.values())) == len(expected)
        for account_id, state in states.items():
            record = await fetch_warming_state(account_id)
            assert record is not None
            assert record.state == state
            if account_id in expected:
                assert record.run_id == run_ids[account_id]
            else:
                assert record.run_id == "old-generation"
    finally:
        await _cancel_runtime_tasks()


@pytest.mark.asyncio
async def test_reconcile_keeps_live_task_and_replaces_completed_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def loop(_account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
        await asyncio.Event().wait()

    monkeypatch.setattr(_runtime, "_warming_loop", loop)
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )
    for account_id in ("live", "done"):
        await create_account(AccountCreate(account_id=account_id))
        await upsert_warming_state(
            WarmingStateWrite(account_id=account_id, state="active", run_id=f"{account_id}-old")
        )

    async def wait_forever() -> None:
        await asyncio.Event().wait()

    async def finish() -> None:
        await asyncio.sleep(0)

    live_task = asyncio.create_task(wait_forever())
    done_task = asyncio.create_task(finish())
    await done_task
    warming._RUNTIME.update({"live": live_task, "done": done_task})

    try:
        await warming.reconcile_warming_runtime()

        assert warming._RUNTIME["live"] is live_task
        assert warming._RUNTIME["done"] is not done_task
        live_record = await fetch_warming_state("live")
        done_record = await fetch_warming_state("done")
        assert live_record is not None
        assert live_record.run_id == "live-old"
        assert done_record is not None
        assert done_record.run_id != "done-old"
    finally:
        await _cancel_runtime_tasks()


@pytest.mark.asyncio
async def test_repeated_reconcile_is_idempotent_for_running_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    starts = 0

    async def loop(_account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
        nonlocal starts
        starts += 1
        await asyncio.Event().wait()

    monkeypatch.setattr(_runtime, "_warming_loop", loop)
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="acc-1", state="sleeping", run_id="old")
    )

    try:
        await warming.reconcile_warming_runtime()
        first_task = warming._RUNTIME["acc-1"]
        first_record = await fetch_warming_state("acc-1")
        await asyncio.sleep(0)
        await warming.reconcile_warming_runtime()
        second_record = await fetch_warming_state("acc-1")

        assert warming._RUNTIME["acc-1"] is first_task
        assert starts == 1
        assert first_record is not None
        assert second_record is not None
        assert second_record.run_id == first_record.run_id
    finally:
        await _cancel_runtime_tasks()
