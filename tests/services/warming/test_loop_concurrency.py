"""The fleet-wide warming slot bounds Telegram requests, not whole cycles.

A slot is held only around one gateway call, so an account in a human pause never
starves the rest of the fleet, while the number of requests in flight stays capped.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core._config_warming import WarmingSettings
from core.config import settings
from core.db import create_account, save_warming_settings
from schemas.accounts import AccountCreate
from schemas.telegram_actions import ActionResult, SetOnline
from services import warming
from services.warming import _seams, _steps
from tests.services.warming._support import _Recorder, _seed_channel

if TYPE_CHECKING:
    from schemas.telegram_actions import TelegramAction


class _GatewayProbe:
    """Stands in for the core gateway, recording the peak of concurrent requests."""

    def __init__(self) -> None:
        self.recorder = _Recorder()
        self.current = 0
        self.peak = 0
        self.dispatched: dict[str, asyncio.Event] = {}

    def seen(self, account_id: str) -> asyncio.Event:
        return self.dispatched.setdefault(account_id, asyncio.Event())

    async def __call__(
        self,
        account_id: str,
        action: TelegramAction,
        *,
        domain: str = "warming",  # noqa: ARG002 - signature parity
        device_type: str | None = None,  # noqa: ARG002 - signature parity
    ) -> ActionResult:
        self.current += 1
        self.peak = max(self.peak, self.current)
        try:
            await asyncio.sleep(0.01)  # hold the slot so unbounded peers would overlap
            return await self.recorder.execute(account_id, action)
        finally:
            self.current -= 1
            self.seen(account_id).set()


async def _seed_accounts(count: int) -> None:
    await _seed_channel()
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )
    for i in range(count):
        await create_account(AccountCreate(account_id=f"acc-{i}"))


def test_default_slot_count_is_sixteen() -> None:
    assert WarmingSettings.model_fields["cycle_concurrency"].default == 16


@pytest.mark.asyncio
async def test_slot_is_not_held_through_a_human_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    # One slot, two accounts. Each account's reading pause waits until the OTHER
    # account has dispatched: if a slot were held across the pause (the old
    # cycle-wide hold), the first account would sit on it and the wait would time out.
    probe = _GatewayProbe()
    monkeypatch.setattr(_seams, "_gateway_execute", probe)
    _seams._dispatch_slots = asyncio.Semaphore(1)
    await _seed_accounts(2)
    peers = {"acc-0": "acc-1", "acc-1": "acc-0"}
    paused: list[str] = []

    async def pause_until_peer_dispatched(_min: float, _max: float) -> None:
        task = asyncio.current_task()
        assert task is not None
        paused.append(task.get_name())
        await asyncio.wait_for(probe.seen(peers[task.get_name()]).wait(), timeout=2)

    monkeypatch.setattr(_steps, "_human_pause", pause_until_peer_dispatched)

    async with asyncio.TaskGroup() as group:
        tasks = [
            group.create_task(warming.run_loop_iteration(account), name=account)
            for account in peers
        ]

    assert all(task.result().status == "ok" for task in tasks)
    assert sorted(set(paused)) == sorted(peers)
    assert probe.peak == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("slots", [1, 3])
async def test_requests_in_flight_never_exceed_the_slot_count(
    monkeypatch: pytest.MonkeyPatch,
    slots: int,
) -> None:
    probe = _GatewayProbe()
    monkeypatch.setattr(_seams, "_gateway_execute", probe)
    _seams._dispatch_slots = asyncio.Semaphore(slots)

    await asyncio.gather(*(_seams.execute(f"acc-{i}", SetOnline()) for i in range(8)))

    assert probe.peak == slots


@pytest.mark.asyncio
async def test_a_cycle_fleet_shares_the_configured_slots(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = _GatewayProbe()
    monkeypatch.setattr(_seams, "_gateway_execute", probe)
    monkeypatch.setattr(settings.warming, "cycle_concurrency", 2)
    _seams._dispatch_slots = asyncio.Semaphore(settings.warming.cycle_concurrency)
    await _seed_accounts(4)

    await asyncio.gather(*(warming.run_loop_iteration(f"acc-{i}") for i in range(4)))

    assert probe.peak == 2


@pytest.mark.asyncio
async def test_slot_is_released_when_the_gateway_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(
        _account_id: str,
        _action: TelegramAction,
        *,
        domain: str,
        device_type: str | None = None,  # noqa: ARG001 - signature parity
    ) -> ActionResult:
        raise RuntimeError(domain)

    monkeypatch.setattr(_seams, "_gateway_execute", boom)
    _seams._dispatch_slots = asyncio.Semaphore(1)

    with pytest.raises(RuntimeError):
        await _seams.execute("acc-0", SetOnline())

    assert not _seams._dispatch_slots.locked()


@pytest.mark.asyncio
async def test_slot_is_released_when_the_dispatch_is_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()

    async def hang(
        _account_id: str,
        _action: TelegramAction,
        *,
        domain: str,
        device_type: str | None = None,  # noqa: ARG001 - signature parity
    ) -> ActionResult:
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError(domain)  # pragma: no cover - cancelled above

    monkeypatch.setattr(_seams, "_gateway_execute", hang)
    _seams._dispatch_slots = asyncio.Semaphore(1)
    task = asyncio.create_task(_seams.execute("acc-0", SetOnline()))
    await entered.wait()
    assert _seams._dispatch_slots.locked()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not _seams._dispatch_slots.locked()


@pytest.mark.asyncio
async def test_a_generation_retired_while_queued_never_dispatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The lease is checked once the slot is won, so a Stop that lands while the
    # request waits in the queue still keeps it from reaching Telegram.
    release = asyncio.Event()
    calls: list[str] = []

    async def gateway(
        account_id: str,
        action: TelegramAction,
        *,
        domain: str = "warming",  # noqa: ARG001 - signature parity
        device_type: str | None = None,  # noqa: ARG001 - signature parity
    ) -> ActionResult:
        calls.append(account_id)
        await release.wait()
        return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)

    monkeypatch.setattr(_seams, "_gateway_execute", gateway)
    _seams._dispatch_slots = asyncio.Semaphore(1)
    holder = asyncio.create_task(_seams.execute("acc-0", SetOnline()))
    await asyncio.sleep(0)
    _seams.activate_lease("acc-1", "run-1")
    with _seams.lease_scope("acc-1", "run-1"):
        queued = asyncio.create_task(_seams.execute("acc-1", SetOnline()))
    await asyncio.sleep(0)
    _seams.revoke_lease("acc-1", "run-1")
    release.set()

    await holder
    with pytest.raises(_seams.WarmingLeaseRevokedError) as raised:
        await queued

    assert not isinstance(raised.value, _seams.WarmingLeaseLostMidDispatchError)
    assert calls == ["acc-0"]
