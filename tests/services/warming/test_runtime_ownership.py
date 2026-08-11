"""Warming task ownership, bounded shutdown and generation lease fencing."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (
    create_account,
    fetch_account,
    fetch_warming_state,
    mark_promoted_to_nc,
    save_warming_settings,
)
from schemas.accounts import AccountCreate
from schemas.warming import StartWarmingRequest, StopWarmingRequest
from services import warming
from services.warming import _runtime, _seams
from tests.services.warming._support import (
    _iteration,
    _no_quiet_days,
    _seed_channel,
    _seed_warming_account,
    _set_settings,
)

if TYPE_CHECKING:
    from schemas.telegram_actions import ActionResult, TelegramAction
    from schemas.warming import WarmingState


async def _ready_account() -> None:
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )
    await create_account(AccountCreate(account_id="acc-1"))


class _CancellationSuppressingLoop:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancellations = 0

    async def __call__(self, account_id: str, *, run_id: str | None = None) -> None:
        del account_id, run_id
        self.started.set()
        while not self.release.is_set():
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancellations += 1


@pytest.mark.asyncio
async def test_stop_retains_suppressed_task_and_restart_waits_for_quiescence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stubborn = _CancellationSuppressingLoop()
    monkeypatch.setattr(_runtime, "_warming_loop", stubborn)
    monkeypatch.setattr(settings.warming, "stop_cancel_timeout_seconds", 0.01)
    await _ready_account()

    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))
    await stubborn.started.wait()
    original = warming._RUNTIME["acc-1"]

    stopped = await warming.stop_warming(StopWarmingRequest(account_id="acc-1"))

    assert stopped.state == "error"
    assert stopped.last_event == "stop_timeout"
    assert warming._RUNTIME["acc-1"] is original
    assert not original.done()

    with pytest.raises(warming.WarmingNotReadyError, match="still stopping"):
        await warming.start_warming(StartWarmingRequest(account_id="acc-1"))
    assert warming._RUNTIME["acc-1"] is original

    stubborn.release.set()
    await asyncio.wait_for(original, timeout=1)
    await asyncio.sleep(0)  # run the ownership done callback
    assert "acc-1" not in warming._RUNTIME


@pytest.mark.asyncio
async def test_shutdown_is_bounded_and_keeps_non_terminal_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stubborn = _CancellationSuppressingLoop()
    monkeypatch.setattr(_runtime, "_warming_loop", stubborn)
    monkeypatch.setattr(settings.warming, "stop_cancel_timeout_seconds", 0.01)
    await _ready_account()
    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))
    await stubborn.started.wait()
    task = warming._RUNTIME["acc-1"]

    await asyncio.wait_for(warming.shutdown_warming_runtime(), timeout=0.2)

    assert warming._RUNTIME["acc-1"] is task
    assert not task.done()
    stubborn.release.set()
    await asyncio.wait_for(task, timeout=1)
    await asyncio.sleep(0)
    assert "acc-1" not in warming._RUNTIME


@pytest.mark.asyncio
async def test_late_stop_completion_settles_same_marker_to_idle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stubborn = _CancellationSuppressingLoop()
    monkeypatch.setattr(_runtime, "_warming_loop", stubborn)
    monkeypatch.setattr(settings.warming, "stop_cancel_timeout_seconds", 0.01)
    await _ready_account()
    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))
    await stubborn.started.wait()
    task = warming._RUNTIME["acc-1"]

    stopped = await warming.stop_warming(StopWarmingRequest(account_id="acc-1"))
    assert stopped.state == "error"

    stubborn.release.set()
    await asyncio.wait_for(task, timeout=1)
    settled = None
    for _ in range(100):
        settled = await fetch_warming_state("acc-1")
        if settled is not None and settled.state == "idle":
            break
        await asyncio.sleep(0.001)
    assert settled is not None
    assert settled.state == "idle"
    assert settled.run_id is None
    assert settled.last_event == "stopped_after_timeout"


@pytest.mark.asyncio
async def test_promote_refuses_non_quiescent_warming_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stubborn = _CancellationSuppressingLoop()
    monkeypatch.setattr(_runtime, "_warming_loop", stubborn)
    monkeypatch.setattr(settings.warming, "stop_cancel_timeout_seconds", 0.01)
    await _ready_account()
    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))
    await stubborn.started.wait()
    task = warming._RUNTIME["acc-1"]

    with pytest.raises(warming.WarmingTaskNotQuiescentError):
        await warming.promote_to_neurocomment("acc-1")

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.promoted_to_nc is False
    stubborn.release.set()
    await asyncio.wait_for(task, timeout=1)


@pytest.mark.asyncio
async def test_handoff_and_delete_refuse_non_quiescent_warming_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.accounts.lifecycle import remove_account  # noqa: PLC0415

    stubborn = _CancellationSuppressingLoop()
    monkeypatch.setattr(_runtime, "_warming_loop", stubborn)
    monkeypatch.setattr(settings.warming, "stop_cancel_timeout_seconds", 0.01)
    await _ready_account()
    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))
    await stubborn.started.wait()
    task = warming._RUNTIME["acc-1"]
    await mark_promoted_to_nc("acc-1")

    with pytest.raises(warming.WarmingTaskNotQuiescentError):
        await warming.handoff_to_neurocomment("acc-1")
    with pytest.raises(warming.WarmingTaskNotQuiescentError):
        await remove_account("acc-1")

    account = await fetch_account("acc-1")
    record = await fetch_warming_state("acc-1")
    assert account is not None
    assert record is not None
    assert record.nc_handed_off is False
    stubborn.release.set()
    await asyncio.wait_for(task, timeout=1)


@pytest.mark.asyncio
async def test_shutdown_is_bounded_when_purge_suppresses_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def stubborn_purge() -> None:
        started.set()
        while not release.is_set():
            with suppress(asyncio.CancelledError):
                await release.wait()

    monkeypatch.setattr(settings.warming, "stop_cancel_timeout_seconds", 0.01)
    task = asyncio.create_task(stubborn_purge())
    _runtime._PURGE_TASK = task
    await started.wait()

    await asyncio.wait_for(warming.shutdown_warming_runtime(), timeout=0.2)

    assert _runtime._PURGE_TASK is task
    assert not task.done()
    release.set()
    await asyncio.wait_for(task, timeout=1)
    _runtime._PURGE_TASK = None


@pytest.mark.asyncio
async def test_lease_fences_before_and_after_gateway_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def gateway(
        account_id: str,
        action: TelegramAction,
        *,
        domain: str,
    ) -> ActionResult:
        from schemas.telegram_actions import ActionResult  # noqa: PLC0415

        del domain
        calls.append(action.action_type)
        _seams.revoke_lease(account_id, "run-1")
        return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)

    monkeypatch.setattr(_seams, "_gateway_execute", gateway)
    _seams.activate_lease("acc-1", "run-1")
    from schemas.telegram_actions import SetOnline  # noqa: PLC0415

    with _seams.lease_scope("acc-1", "run-1"):
        with pytest.raises(_seams.WarmingLeaseRevokedError):
            await _seams.execute("acc-1", SetOnline(online=True))
        with pytest.raises(_seams.WarmingLeaseRevokedError):
            await _seams.execute("acc-1", SetOnline(online=True))
    assert calls == ["set_online"]


@pytest.mark.asyncio
async def test_uncertain_dispatched_rpc_keeps_full_budget_reserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.warming._loop import run_loop_iteration  # noqa: PLC0415

    await _ready_account()
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)
    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))
    runtime_task = warming._RUNTIME["acc-1"]
    runtime_task.cancel()
    with suppress(asyncio.CancelledError):
        await runtime_task
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.run_id is not None
    run_id = record.run_id

    async def gateway(
        account_id: str,
        action: TelegramAction,
        *,
        domain: str,
    ) -> ActionResult:
        from schemas.telegram_actions import ActionResult  # noqa: PLC0415

        del domain
        _seams.revoke_lease(account_id, run_id)
        return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)

    monkeypatch.setattr(_seams, "_gateway_execute", gateway)
    _seams.activate_lease("acc-1", run_id)
    with _seams.lease_scope("acc-1", run_id), pytest.raises(_seams.WarmingLeaseRevokedError):
        await run_loop_iteration("acc-1", run_id=run_id)

    booked = await fetch_warming_state("acc-1")
    assert booked is not None
    assert booked.daily_actions == settings.warming.phase_daily_cap["intro"]


@pytest.mark.asyncio
async def test_cancel_during_dispatched_rpc_counts_attempt_as_spent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_quiet_days(monkeypatch)
    dispatched = asyncio.Event()

    async def blocked_after_dispatch(
        account_id: str,
        action: TelegramAction,
    ) -> ActionResult:
        del account_id, action
        dispatched.set()
        await asyncio.Event().wait()
        raise AssertionError

    monkeypatch.setattr(_seams, "execute", blocked_after_dispatch)
    await _seed_warming_account()
    task = asyncio.create_task(_iteration())
    await asyncio.wait_for(dispatched.wait(), timeout=1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.daily_actions == 1


@pytest.mark.asyncio
async def test_cancel_during_start_publish_leaves_no_active_orphan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _ready_account()
    real_set_state = _runtime._set_state
    cancelled = False

    async def commit_then_cancel(account_id: str, state: WarmingState, **changes):  # type: ignore[no-untyped-def]
        nonlocal cancelled
        result = await real_set_state(account_id, state, **changes)
        if state == "active" and changes.get("last_event") == "queued" and not cancelled:
            cancelled = True
            current = asyncio.current_task()
            assert current is not None
            current.cancel()
            await asyncio.sleep(0)
        return result

    monkeypatch.setattr(_runtime, "_set_state", commit_then_cancel)

    with pytest.raises(asyncio.CancelledError):
        await warming.start_warming(StartWarmingRequest(account_id="acc-1"))

    state = await fetch_warming_state("acc-1")
    assert state is not None
    assert state.state == "idle"
    assert state.run_id is None
    assert "acc-1" not in warming._RUNTIME
