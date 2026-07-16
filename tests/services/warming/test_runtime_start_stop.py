"""Warming tests split from the former service test module: test_runtime_start_stop.py."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from core.config import settings
from core.db import (
    create_account,
    fetch_warming_state,
    save_warming_settings,
    set_listener_account_id,
    set_listener_running,
    upsert_warming_state,
)
from schemas.accounts import AccountCreate
from schemas.warming import (
    StartWarmingRequest,
    StopWarmingRequest,
    WarmingStateWrite,
)
from services import warming
from services.warming import _runtime
from tests.services.warming._support import (
    _fake_loop,
    _seed_ready_account,
    _set_settings,
)


@pytest.mark.asyncio
async def test_start_and_stop_warming_manage_the_task(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_loop(_account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    await create_account(AccountCreate(account_id="acc-1"))
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )

    started = await warming.start_warming(StartWarmingRequest(account_id="acc-1"))
    assert started.state == "active"
    task = warming._RUNTIME["acc-1"]
    assert not task.done()

    stopped = await warming.stop_warming(StopWarmingRequest(account_id="acc-1"))
    assert stopped.state == "idle"
    assert "acc-1" not in warming._RUNTIME
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_stop_warming_without_running_task_is_safe() -> None:
    await create_account(AccountCreate(account_id="acc-1"))

    stopped = await warming.stop_warming(StopWarmingRequest(account_id="acc-1"))

    assert stopped.state == "idle"


@pytest.mark.asyncio
async def test_start_warming_persists_chosen_target_days(monkeypatch: pytest.MonkeyPatch) -> None:
    # The day slider's value reaches the warming-state row (was silently dropped).
    monkeypatch.setattr(_runtime, "_warming_loop", _fake_loop)
    await create_account(AccountCreate(account_id="acc-1"))
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)

    await warming.start_warming(StartWarmingRequest(account_id="acc-1", target_days=5))

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.target_days == 5


@pytest.mark.asyncio
async def test_start_warming_defaults_target_to_config(monkeypatch: pytest.MonkeyPatch) -> None:
    # Omitting target_days falls back to the configured warmed_min_days floor.
    monkeypatch.setattr(_runtime, "_warming_loop", _fake_loop)
    monkeypatch.setattr(settings.neurocomment, "warmed_min_days", 9)
    await create_account(AccountCreate(account_id="acc-1"))
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)

    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.target_days == 9


@pytest.mark.asyncio
async def test_start_warming_rejects_the_running_listener(monkeypatch: pytest.MonkeyPatch) -> None:
    # Reciprocal of neurocomment's listener guard: the active listener account
    # cannot be dragged into warming (the two runtimes must never share a session).
    monkeypatch.setattr(_runtime, "_warming_loop", _fake_loop)
    await create_account(AccountCreate(account_id="acc-1"))
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)
    await set_listener_account_id("acc-1")
    await set_listener_running(running=True)

    with pytest.raises(warming.AccountIsListenerError):
        await warming.start_warming(StartWarmingRequest(account_id="acc-1"))
    assert "acc-1" not in warming._RUNTIME


@pytest.mark.asyncio
async def test_start_warming_allows_a_paused_listener(monkeypatch: pytest.MonkeyPatch) -> None:
    # A merely-remembered (paused) listener is not running, so warming is allowed.
    monkeypatch.setattr(_runtime, "_warming_loop", _fake_loop)
    await create_account(AccountCreate(account_id="acc-1"))
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)
    await set_listener_account_id("acc-1")
    await set_listener_running(running=False)

    started = await warming.start_warming(StartWarmingRequest(account_id="acc-1"))
    assert started.state == "active"


@pytest.mark.asyncio
async def test_restart_while_warming_keeps_original_target_days(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A restart while the account is still warming must keep the ORIGINAL pick
    # (mirrors the persona rule). Honouring a smaller target here would complete
    # a still-anchored account on its next iteration.
    monkeypatch.setattr(_runtime, "_warming_loop", _fake_loop)
    await create_account(AccountCreate(account_id="acc-1"))
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)
    old_start = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1", state="sleeping", started_at=old_start, target_days=14
        ),
    )

    # Operator restarts with a *different* target while it is still warming.
    await warming.start_warming(StartWarmingRequest(account_id="acc-1", target_days=3))

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.target_days == 14  # original kept, not the new 3


@pytest.mark.asyncio
async def test_genuine_restart_honours_new_target_days(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A genuine (re)start from idle honours the new value (started_at is re-stamped).
    monkeypatch.setattr(_runtime, "_warming_loop", _fake_loop)
    await create_account(AccountCreate(account_id="acc-1"))
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)
    old_start = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="idle",
            started_at=old_start,
            stopped_at=(datetime.now(UTC) - timedelta(days=1)).isoformat(),
            target_days=14,
        ),
    )

    await warming.start_warming(StartWarmingRequest(account_id="acc-1", target_days=3))

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.target_days == 3  # new value honoured on a genuine restart


@pytest.mark.asyncio
async def test_start_warming_unknown_account_raises() -> None:
    with pytest.raises(warming.UnknownAccountError):
        await warming.start_warming(StartWarmingRequest(account_id="ghost"))


@pytest.mark.asyncio
async def test_start_warming_blocks_not_ready_account() -> None:
    await create_account(AccountCreate(account_id="acc-1"))  # new, no proxy/channels
    with pytest.raises(warming.WarmingNotReadyError) as excinfo:
        await warming.start_warming(StartWarmingRequest(account_id="acc-1"))
    assert excinfo.value.reasons
    assert "acc-1" not in warming._RUNTIME


@pytest.mark.asyncio
async def test_start_warming_ready_account_records_proxy_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_loop(_account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    await _seed_ready_account("acc-1")

    card = await warming.start_warming(StartWarmingRequest(account_id="acc-1"))

    assert card.state == "active"
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.proxy_snapshot is not None
    assert "1.2.3.4" in record.proxy_snapshot


@pytest.mark.asyncio
async def test_manual_start_clears_stale_next_run_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Manual Start must fire immediately, not honour an old persisted schedule."""

    async def fake_loop(_account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    await _seed_ready_account("acc-1")
    far_future = (datetime.now(UTC) + timedelta(hours=12)).isoformat()
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="sleeping",
            cycles_completed=0,
            next_run_at=far_future,
        ),
    )

    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.next_run_at is None


@pytest.mark.asyncio
async def test_start_warming_clears_stale_action_and_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Right after Start the card must not show the previous run's action/channel
    # (audit П6); the cycle hasn't begun yet, so they must be cleared at queue.
    async def fake_loop(_account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    await _seed_ready_account("acc-1")
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="sleeping",
            last_action="send_dm",
            last_channel="old-chan",
        ),
    )

    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.last_event == "queued"
    assert record.last_action is None
    assert record.last_channel is None


@pytest.mark.asyncio
async def test_start_warming_preserves_started_at_on_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Restarting an already-warming account keeps the original stint anchor so
    # "дней в прогреве" counts from the first start, not this restart (audit П7).
    async def fake_loop(_account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    await _seed_ready_account("acc-1")
    original = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    await upsert_warming_state(
        WarmingStateWrite(account_id="acc-1", state="sleeping", started_at=original),
    )

    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.started_at == original


@pytest.mark.asyncio
async def test_start_warming_from_idle_stamps_started_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A genuine start (no prior warming row) stamps a fresh anchor.
    async def fake_loop(_account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    await _seed_ready_account("acc-1")

    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.started_at is not None


@pytest.mark.asyncio
async def test_start_warming_refreshes_pairs_before_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    async def fake_refresh() -> None:
        calls.append("refresh")

    async def fake_loop(_account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
        calls.append("loop")

    monkeypatch.setattr("services.warming._runtime._refresh_dialogue_pairs", fake_refresh)
    monkeypatch.setattr("services.warming._runtime._warming_loop", fake_loop)
    from schemas.warming import WarmingReadiness  # noqa: PLC0415

    monkeypatch.setattr(
        "services.warming._runtime.evaluate_readiness",
        lambda *_a, **_kw: WarmingReadiness(ready=True, reasons=[]),
    )

    await create_account(AccountCreate(account_id="acc-a"))
    await _set_settings(chat=True, reactions=False, key="test")

    await warming.start_warming(StartWarmingRequest(account_id="acc-a"))

    # Verify order
    assert calls == ["refresh", "loop"]


@pytest.mark.asyncio
async def test_start_warming_mints_fresh_run_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """P1.2: each manual start writes a new run_id distinct from the previous one."""

    async def fake_loop(_account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )
    await create_account(AccountCreate(account_id="acc-1"))

    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))
    state_first = await fetch_warming_state("acc-1")
    assert state_first is not None
    first_run_id = state_first.run_id
    assert first_run_id is not None

    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))
    state_second = await fetch_warming_state("acc-1")
    assert state_second is not None
    assert state_second.run_id is not None
    assert state_second.run_id != first_run_id
