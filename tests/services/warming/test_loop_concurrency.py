"""FIX #1b: the fleet-wide cycle-concurrency semaphore bounds simultaneous cycles."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import create_account, save_warming_settings
from schemas.accounts import AccountCreate
from schemas.warming import WarmingCycleResult
from services import warming
from services.warming import _loop, _seams
from tests.services.warming._support import _Recorder, _seed_channel

if TYPE_CHECKING:
    from schemas.warming import WarmingCycleRequest, WarmingSettingsSecret
    from services.warming._cycle import _OnStep


class _ConcurrencyProbe:
    """Stands in for ``run_one_cycle``, recording the peak concurrent entries.

    Holds each slot briefly so that, absent the semaphore, overlapping loop
    iterations would pile up and push ``peak`` above one.
    """

    def __init__(self) -> None:
        self.current = 0
        self.peak = 0

    async def cycle(
        self,
        data: WarmingCycleRequest,
        *,
        secret: WarmingSettingsSecret | None = None,  # noqa: ARG002 - signature parity
        on_step: _OnStep | None = None,  # noqa: ARG002 - signature parity
    ) -> WarmingCycleResult:
        self.current += 1
        self.peak = max(self.peak, self.current)
        try:
            await asyncio.sleep(0.1)  # hold the slot so peers overlap if unbounded
        finally:
            self.current -= 1
        return WarmingCycleResult(account_id=data.account_id, status="ok")


async def _seed_three_gate_passing_accounts(monkeypatch: pytest.MonkeyPatch) -> None:
    # Readiness + quiet-day off so all three iterations reach the cycle; execute is
    # stubbed only defensively (the patched run_one_cycle never dispatches).
    monkeypatch.setattr(settings.warming, "quiet_day_weekday_probability", 0.0)
    monkeypatch.setattr(settings.warming, "quiet_day_weekend_probability", 0.0)
    monkeypatch.setattr(_seams, "execute", _Recorder().execute)
    await _seed_channel()
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )
    for i in range(3):
        await create_account(AccountCreate(account_id=f"acc-{i}"))


@pytest.mark.asyncio
async def test_cycle_semaphore_bounds_concurrent_cycles(monkeypatch: pytest.MonkeyPatch) -> None:
    # Bound 1 → the loop never runs two Telegram-heavy cycles at once, even when a
    # reconcile fires several accounts' iterations together (the restart-burst fix).
    probe = _ConcurrencyProbe()
    monkeypatch.setattr(_loop, "run_one_cycle", probe.cycle)
    await _seed_three_gate_passing_accounts(monkeypatch)
    _loop._cycle_semaphore = asyncio.Semaphore(1)

    await asyncio.gather(*(warming.run_loop_iteration(f"acc-{i}") for i in range(3)))

    assert probe.peak == 1


@pytest.mark.asyncio
async def test_cycle_semaphore_permits_parallelism(monkeypatch: pytest.MonkeyPatch) -> None:
    # Control: a higher bound genuinely runs cycles in parallel, so the peak==1
    # above is the semaphore holding — not an artefact of serial execution.
    probe = _ConcurrencyProbe()
    monkeypatch.setattr(_loop, "run_one_cycle", probe.cycle)
    await _seed_three_gate_passing_accounts(monkeypatch)
    _loop._cycle_semaphore = asyncio.Semaphore(3)

    await asyncio.gather(*(warming.run_loop_iteration(f"acc-{i}") for i in range(3)))

    assert probe.peak > 1
