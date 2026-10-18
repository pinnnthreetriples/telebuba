"""A mobile stint warms in its Redroid pod; a desktop stint never touches a pod.

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
from schemas.emulator import OpenMobileResult, PlaybookResult
from services import warming
from services.accounts.mobile_emulator import EmulatorBusyError, EmulatorRefusedError
from services.warming import _mobile_cycle, _seams
from services.warming._mobile_cycle import session_seconds
from services.warming.daily_stats import stats_day
from tests.services.warming._support import _no_quiet_days, _ok, _seed_warming_account

if TYPE_CHECKING:
    from schemas.device_fingerprint import DeviceType
    from schemas.telegram_actions import ActionResult, TelegramAction


class _Pod:
    """Records the pod calls; ``open_error``/``run_error`` make that step refuse."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.signed_in = True
        self.open_error: Exception | None = None
        self.run_error: BaseException | None = None
        self.activity = PlaybookResult(channels_read=2, scrolls=9, stories_watched=3, reactions=1)

    async def open(self, account_id: str) -> OpenMobileResult:
        self.calls.append("open")
        if self.open_error is not None:
            raise self.open_error
        return OpenMobileResult(account_id=account_id, status="started", signed_in=self.signed_in)

    async def run(self, account_id: str, duration_sec: int) -> PlaybookResult:
        del account_id
        self.calls.append(f"run:{duration_sec}")
        if self.run_error is not None:
            raise self.run_error
        return self.activity

    async def stop(self, account_id: str) -> None:
        del account_id
        self.calls.append("stop")


@pytest_asyncio.fixture
async def pod(monkeypatch: pytest.MonkeyPatch) -> _Pod:
    """The fake pod, the emulator on, and one warming account ready for a cycle."""
    _no_quiet_days(monkeypatch)
    await _seed_warming_account()
    fake = _Pod()
    monkeypatch.setattr(settings.emulator, "enabled", True)
    monkeypatch.setattr(_mobile_cycle, "open_account_mobile", fake.open)
    monkeypatch.setattr(_mobile_cycle, "run_mobile_activity", fake.run)
    monkeypatch.setattr(_mobile_cycle, "stop_account_pod", fake.stop)
    return fake


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


@pytest.mark.asyncio
async def test_a_desktop_stint_warms_through_telethon_and_never_touches_a_pod(
    pod: _Pod, telethon: list[str]
) -> None:
    await _iterate_on("desktop")

    assert telethon, "the Telethon cycle ran"
    assert pod.calls == []


@pytest.mark.asyncio
async def test_a_mobile_stint_opens_its_pod_spends_the_visit_and_stops_it(
    pod: _Pod, telethon: list[str]
) -> None:
    await _iterate_on("mobile")

    assert pod.calls[0] == "open"
    assert pod.calls[1].startswith("run:")
    assert pod.calls[2:] == ["stop"]
    assert telethon == [], "no MTProto action on a mobile stint"
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.state == "sleeping"
    assert record.last_event == "cycle:ok"
    assert record.cycles_completed == 1
    day = await stats_day("acc-1", datetime.now(UTC))
    [stats] = await list_warming_daily_stats("acc-1", day)
    assert (stats.cycles, stats.reads, stats.reactions, stats.failures) == (1, 2, 1, 0)


@pytest.mark.asyncio
async def test_a_mobile_stint_with_the_emulator_off_stays_on_telethon(
    pod: _Pod, telethon: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.emulator, "enabled", False)

    await _iterate_on("mobile")

    assert telethon
    assert pod.calls == []


@pytest.mark.asyncio
async def test_a_pod_that_will_not_open_fails_the_cycle_and_the_loop_goes_on(
    pod: _Pod, telethon: list[str]
) -> None:
    pod.open_error = EmulatorRefusedError("emulator_proxy_missing")

    await _iterate_on("mobile")

    assert pod.calls == ["open", "stop"]
    assert telethon == [], "no fallback to Telethon"
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.state == "sleeping", "retried next cycle, not parked in error"
    assert record.last_event == "cycle:failed"
    assert record.last_error == "emulator_proxy_missing"
    assert record.next_run_at is not None


@pytest.mark.asyncio
async def test_an_app_left_signed_out_fails_the_cycle_without_a_session(
    pod: _Pod, telethon: list[str]
) -> None:
    pod.signed_in = False

    await _iterate_on("mobile")

    assert pod.calls == ["open", "stop"]
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.last_error == "emulator_not_signed_in"
    assert telethon == []


@pytest.mark.asyncio
async def test_a_failed_session_still_stops_the_pod(pod: _Pod, telethon: list[str]) -> None:
    pod.run_error = EmulatorRefusedError("emulator_field_missing")

    await _iterate_on("mobile")

    assert pod.calls[0] == "open"
    assert pod.calls[-1] == "stop"
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert (record.state, record.last_error) == ("sleeping", "emulator_field_missing")
    assert telethon == []


@pytest.mark.asyncio
async def test_a_pod_busy_with_the_operator_is_left_running(pod: _Pod, telethon: list[str]) -> None:
    pod.open_error = EmulatorBusyError()

    await _iterate_on("mobile")

    assert pod.calls == ["open"], "the operator's pod is never stopped"
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert (record.state, record.last_error) == ("sleeping", "emulator_busy")
    assert telethon == []


@pytest.mark.asyncio
async def test_a_stop_mid_session_still_stops_the_pod(pod: _Pod, telethon: list[str]) -> None:
    pod.run_error = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await _iterate_on("mobile")

    assert pod.calls[-1] == "stop"
    assert telethon == []


@pytest.mark.parametrize(
    ("budget", "expected"),
    [(None, 900), (0, 60), (1, 60), (4, 120), (10**6, 900)],
)
def test_a_visit_buys_seconds_by_its_budget_within_the_cap(
    budget: int | None, expected: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.emulator, "activity_max_seconds", 900)

    assert session_seconds(budget) == expected
