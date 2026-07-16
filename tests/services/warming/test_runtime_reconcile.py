"""Warming tests split from the former service test module: test_runtime_reconcile.py."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import delete as sa_delete

from core.config import settings
from core.db import (
    _accounts,
    _get_engine,
    _warming_account_state,
    create_account,
    fetch_warming_state,
    list_dialogue_pairs,
    purge_dialogue_messages_older_than,
    purge_logs_older_than,
    purge_sent_hashes_older_than,
    save_warming_settings,
    upsert_warming_state,
)
from schemas.accounts import AccountCreate
from schemas.warming import (
    StartWarmingRequest,
    WarmingCycleResult,
    WarmingStateWrite,
)
from services import warming
from services.warming import _runner, _runtime
from tests.services.warming._support import (
    _no_initial_delay,
    _seed_channel,
    _seed_ready_account,
)


@pytest.mark.asyncio
async def test_reconcile_warming_runtime_restarts_active_loops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[str] = []

    async def fake_loop(account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
        started.append(account_id)
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    # Isolate the restart mechanism; the readiness gate on reconcile is covered
    # by test_reconcile_parks_unready_account_when_enforced (#99).
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="active"))

    await warming.reconcile_warming_runtime()

    assert "acc-1" in warming._RUNTIME
    # Give the loop a single scheduling tick so it actually starts.
    await asyncio.sleep(0)
    assert "acc-1" in started


@pytest.mark.asyncio
async def test_reconcile_warming_runtime_skips_error_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Error accounts must not be auto-resurrected on restart; user has to act."""
    started: list[str] = []

    async def fake_loop(account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
        started.append(account_id)
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    await create_account(AccountCreate(account_id="acc-broken"))
    await upsert_warming_state(WarmingStateWrite(account_id="acc-broken", state="error"))

    await warming.reconcile_warming_runtime()

    assert "acc-broken" not in warming._RUNTIME
    await asyncio.sleep(0)
    assert "acc-broken" not in started


@pytest.mark.asyncio
async def test_reconcile_warming_runtime_builds_dialogue_pairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inter-account chat needs pairs — reconcile must build the graph on startup."""

    async def fake_loop(_account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    for account_id in ("acc-1", "acc-2", "acc-3"):
        await _seed_ready_account(account_id)
        await upsert_warming_state(WarmingStateWrite(account_id=account_id, state="active"))
    assert (await list_dialogue_pairs()) == []

    await warming.reconcile_warming_runtime()

    pairs = await list_dialogue_pairs()
    assert pairs, "reconcile must produce dialogue pairs so inter-account chat works"


@pytest.mark.asyncio
async def test_reconcile_purges_stale_history(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reconcile must run retention so logs/dialogues/hashes don't grow forever."""
    monkeypatch.setattr(settings.warming, "log_retention_days", 30.0)
    monkeypatch.setattr(settings.warming, "dialogue_message_retention_days", 90.0)
    monkeypatch.setattr(settings.warming, "sent_hash_retention_days", 14.0)

    calls: list[str] = []

    async def make_recorder(name: str) -> object:
        async def fake(_cutoff: str) -> int:
            calls.append(name)
            return 0

        return fake

    monkeypatch.setattr(
        "services.warming._purge.purge_logs_older_than",
        await make_recorder("logs"),
    )
    monkeypatch.setattr(
        "services.warming._purge.purge_dialogue_messages_older_than",
        await make_recorder("dialogues"),
    )
    monkeypatch.setattr(
        "services.warming._purge.purge_sent_hashes_older_than",
        await make_recorder("hashes"),
    )

    await warming.reconcile_warming_runtime()

    assert set(calls) == {"logs", "dialogues", "hashes"}
    # Sanity: the real purge_* functions still work at the repo level.
    assert await purge_logs_older_than("1900-01-01") == 0
    assert await purge_dialogue_messages_older_than("1900-01-01") == 0
    assert await purge_sent_hashes_older_than("1900-01-01") == 0


@pytest.mark.asyncio
async def test_reconcile_marks_orphan_state_rows_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_loop(_account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    # Insert state row directly via DB helper, bypassing FK requirement by
    # first creating then deleting the account.
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="active"))

    with _get_engine().begin() as conn:
        conn.execute(
            sa_delete(_warming_account_state).where(
                _warming_account_state.c.account_id == "acc-1",
            ),
        )
        conn.execute(sa_delete(_accounts).where(_accounts.c.account_id == "acc-1"))

    # Re-insert state directly (the FK would block in normal flow, but tests
    # explicitly probe the orphan path).
    with _get_engine().begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        conn.execute(
            _warming_account_state.insert().values(
                account_id="acc-1",
                state="active",
                cycles_completed=0,
                updated_at="2026-01-01T00:00:00+00:00",
            ),
        )

    await warming.reconcile_warming_runtime()

    # Orphan must not be re-scheduled.
    assert "acc-1" not in warming._RUNTIME


@pytest.mark.asyncio
async def test_shutdown_warming_runtime_cancels_all(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_loop(_account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    await create_account(AccountCreate(account_id="acc-1"))
    await create_account(AccountCreate(account_id="acc-2"))
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )
    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))
    await warming.start_warming(StartWarmingRequest(account_id="acc-2"))
    assert len(warming._RUNTIME) == 2

    await warming.shutdown_warming_runtime()

    assert warming._RUNTIME == {}


@pytest.mark.asyncio
async def test_periodic_purge_task_reruns_purge(monkeypatch: pytest.MonkeyPatch) -> None:
    # The background sweep must rerun retention on its interval, not only at
    # startup — otherwise the append-only tables grow unbounded during uptime.
    fired = asyncio.Event()

    async def fake_purge() -> None:
        fired.set()

    monkeypatch.setattr(_runtime, "purge_stale_history", fake_purge)
    # Tiny interval so the first sleep elapses immediately.
    monkeypatch.setattr(settings.warming, "purge_interval_hours", 0.0000001)

    _runtime._start_purge_task()
    try:
        await asyncio.wait_for(fired.wait(), timeout=1.0)
    finally:
        await _runtime._stop_purge_task()

    assert fired.is_set()


@pytest.mark.asyncio
async def test_shutdown_cancels_periodic_purge_task(monkeypatch: pytest.MonkeyPatch) -> None:
    # The purge task is cancelled and awaited cleanly on shutdown (no leak).
    async def fake_purge() -> None:
        return None

    monkeypatch.setattr(_runtime, "purge_stale_history", fake_purge)
    _runtime._start_purge_task()
    task = _runtime._PURGE_TASK
    assert task is not None
    assert not task.done()

    await warming.shutdown_warming_runtime()

    assert _runtime._PURGE_TASK is None
    assert task.cancelled()


@pytest.mark.asyncio
async def test_reconcile_starts_periodic_purge_task(monkeypatch: pytest.MonkeyPatch) -> None:
    # Reconcile (the lifespan entrypoint) must spin up the background sweep.
    async def fake_purge() -> None:
        return None

    monkeypatch.setattr(_runtime, "purge_stale_history", fake_purge)

    await warming.reconcile_warming_runtime()

    assert _runtime._PURGE_TASK is not None
    assert not _runtime._PURGE_TASK.done()


@pytest.mark.asyncio
async def test_reconcile_skips_when_state_already_idle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F3: if state flipped to idle between listing and lock-acquire, do not restart."""
    from core.db import list_warming_states as real_list_states  # noqa: PLC0415

    started: list[str] = []

    async def fake_loop(account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
        started.append(account_id)
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="active"))

    # Simulate the race: list_warming_states sees "active", then between that
    # and the per-account lock, stop_warming flips the row to "idle".
    async def race_list() -> list:  # type: ignore[type-arg]
        records = await real_list_states()
        await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="idle"))
        return records

    monkeypatch.setattr(_runtime, "list_warming_states", race_list)

    await warming.reconcile_warming_runtime()
    await asyncio.sleep(0)

    assert "acc-1" not in warming._RUNTIME
    assert started == []


@pytest.mark.asyncio
async def test_warming_loop_exits_when_state_becomes_idle_after_iteration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1.1: a loop that survives stop_warming must exit on the next idle re-read.

    Simulates a runaway loop by patching ``run_loop_iteration`` to flip state to
    ``idle`` (the stop_warming effect) without actually cancelling the task.
    The loop must observe the idle row on the next fetch and break — *not*
    overwrite it with another cycle_started.
    """
    iterations: list[str] = []

    async def fake_iteration(
        account_id: str,
        *,
        run_id: str | None = None,  # noqa: ARG001
    ) -> WarmingCycleResult:
        iterations.append(account_id)
        await upsert_warming_state(WarmingStateWrite(account_id=account_id, state="idle"))
        return WarmingCycleResult(account_id=account_id, status="ok")

    monkeypatch.setattr(_runner, "run_loop_iteration", fake_iteration)
    monkeypatch.setattr(_runner, "_loop_sleep_seconds", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(_runner, "_initial_delay_seconds", _no_initial_delay)

    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="active"))

    await _runner._warming_loop("acc-1")

    assert iterations == ["acc-1"]
    state = await fetch_warming_state("acc-1")
    assert state is not None
    assert state.state == "idle"


@pytest.mark.asyncio
async def test_run_loop_iteration_bails_when_state_already_idle() -> None:
    """P1.1: a stale iteration started for an already-stopped account is a no-op."""
    from services.warming._loop import run_loop_iteration  # noqa: PLC0415

    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="idle"))

    result = await run_loop_iteration("acc-1")
    assert result.status == "skipped"
    state = await fetch_warming_state("acc-1")
    assert state is not None
    # The early-exit did NOT overwrite ``idle`` with ``cycle_started``.
    assert state.state == "idle"


@pytest.mark.asyncio
async def test_reconcile_parks_unready_account_when_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reconcile must not resurrect an account start_warming would refuse (#99)."""
    started: list[str] = []

    async def fake_loop(account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
        started.append(account_id)
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=True,
        gemini_api_key="",
    )
    await _seed_channel()
    # No proxy => evaluate_readiness fails, exactly as start_warming would.
    await create_account(AccountCreate(account_id="acc-unready"))
    await upsert_warming_state(WarmingStateWrite(account_id="acc-unready", state="active"))

    await warming.reconcile_warming_runtime()

    assert "acc-unready" not in warming._RUNTIME
    await asyncio.sleep(0)
    assert "acc-unready" not in started
    record = await fetch_warming_state("acc-unready")
    assert record is not None
    assert record.state == "error"
    assert record.last_event == "reconcile_not_ready"
    assert record.last_error


@pytest.mark.asyncio
async def test_reconcile_restarts_ready_account_when_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ready account is still restarted with the readiness gate enabled (#99)."""

    async def fake_loop(_account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=True,
        gemini_api_key="",
    )
    await _seed_ready_account("acc-ready")
    await upsert_warming_state(WarmingStateWrite(account_id="acc-ready", state="active"))

    await warming.reconcile_warming_runtime()

    assert "acc-ready" in warming._RUNTIME
