"""The fake pod and the iteration helpers the mobile-cycle tests share.

The pod service is a fake that records each call; the iteration around it is the
real one, so the state write, the schedule and the day's totals are what the loop
really settles.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from core.config import settings
from core.db import fetch_warming_state
from core.repositories.warming_daily_stats import list_warming_daily_stats
from schemas.emulator import OpenMobileResult, PlaybookPlan, PlaybookResult
from services import warming
from services.accounts.mobile_emulator import EmulatorRefusedError
from services.warming import _mobile_cycle, _mobile_prep_state, _seams
from services.warming.daily_stats import stats_day
from tests.services.warming._support import _no_quiet_days, _ok, _seed_warming_account

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from schemas.device_fingerprint import DeviceType
    from schemas.telegram_actions import ActionResult, TelegramAction
    from schemas.warming import WarmingStateRecord
    from schemas.warming_daily_stats import WarmingDailyStats


class _Pod:
    """Records the pod calls; ``open_error``/``run_error`` make that step refuse."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.plans: list[PlaybookPlan | None] = []
        self.signed_in = True
        self.open_error: Exception | None = None
        self.run_error: BaseException | None = None
        self.activity = PlaybookResult(channels_read=2, scrolls=9, stories_watched=3, reactions=1)
        # Set to hold the stop until the test releases it.
        self.stop_gate: asyncio.Event | None = None
        self.parks = True

    async def open(self, account_id: str, *, background: bool = False) -> OpenMobileResult:
        self.calls.append("open:background" if background else "open")
        if self.open_error is not None:
            raise self.open_error
        return OpenMobileResult(account_id=account_id, status="started", signed_in=self.signed_in)

    async def visit(self, account_id: str) -> str | None:
        return "serial" if (await self.open(account_id, background=True)).signed_in else None

    async def run(
        self,
        account_id: str,
        duration_sec: int,
        plan: PlaybookPlan | None = None,
        *,
        opened: str | None = None,
        progress: PlaybookResult | None = None,
    ) -> PlaybookResult:
        del account_id, progress
        self.calls.append(f"run:{duration_sec}:{opened}")
        self.plans.append(plan)
        if self.run_error is not None:
            raise self.run_error
        return self.activity

    async def stop(self, account_id: str) -> None:
        del account_id
        if self.stop_gate is not None:
            await self.stop_gate.wait()
        self.calls.append("stop")

    async def park(self, account_id: str) -> bool:
        del account_id
        self.calls.append("park")
        return self.parks

    async def stop_parked(self, account_id: str) -> None:
        self.calls.append(f"stop_parked:{account_id}")


@pytest_asyncio.fixture
async def pod(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Pod]:
    """The fake pod, the emulator on, and one warming account ready for a cycle."""
    _no_quiet_days(monkeypatch)
    await _seed_warming_account()
    fake = _Pod()
    monkeypatch.setattr(settings.emulator, "enabled", True)
    monkeypatch.setattr(_mobile_cycle, "open_account_mobile", fake.open)
    monkeypatch.setattr(_mobile_cycle, "open_mobile_visit", fake.visit)
    monkeypatch.setattr(_mobile_cycle, "run_mobile_activity", fake.run)
    monkeypatch.setattr(_mobile_cycle, "stop_account_pod", fake.stop)
    monkeypatch.setattr(_mobile_cycle, "_STREAKS", {})
    monkeypatch.setattr(_mobile_cycle, "_RUNNING", set())
    monkeypatch.setattr(_mobile_cycle, "_PARKED", {})
    monkeypatch.setattr(_mobile_prep_state, "STATE", {})
    monkeypatch.setattr(_mobile_prep_state, "NO_CHANNELS", set())
    monkeypatch.setattr(_mobile_cycle, "park_account_pod", fake.park)
    monkeypatch.setattr(_mobile_cycle, "stop_parked_pod", fake.stop_parked)
    # Stopped after every visit unless a test parks it.
    monkeypatch.setattr(settings.emulator, "pause_ttl_seconds", 0)
    yield fake
    if fake.stop_gate is not None:
        fake.stop_gate.set()
    await _stops_done()


async def _stops_done() -> None:
    """Let the detached pod stops finish."""
    await asyncio.gather(*_mobile_cycle._DETACHED)


@pytest.fixture
def telethon(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    sent: list[str] = []

    async def gateway(account_id: str, action: TelegramAction, **_kwargs: object) -> ActionResult:
        sent.append(action.action_type)
        return _ok(account_id, action)

    monkeypatch.setattr(_seams, "_gateway_execute", gateway)
    return sent


async def _iterate_on(device: DeviceType) -> None:
    """One real iteration inside a task bound to ``device``, as the loop task is."""

    async def body() -> None:
        _seams.bind_device(device)
        await warming.run_loop_iteration("acc-1")

    await asyncio.create_task(body())


async def _record() -> WarmingStateRecord:
    record = await fetch_warming_state("acc-1")
    assert record is not None
    return record


async def _today() -> WarmingDailyStats | None:
    day = await stats_day("acc-1", datetime.now(UTC))
    rows = await list_warming_daily_stats("acc-1", day)
    return rows[0] if rows else None


def _refusal(code: str) -> EmulatorRefusedError:
    error = EmulatorRefusedError("emulator_not_running")
    error.code = code
    return error
