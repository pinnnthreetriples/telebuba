"""Warming runs through the device the operator chose at start — and only that one.

The choice is persisted with the stint, carried across a restart of a running stint,
and bound to the loop task from its own generation's row, so a backend restart
resumes warming on the same device. A device the account lacks (or a mobile device
without its app credentials) is refused with a code, never swapped for the other one.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import create_account, fetch_warming_state
from core.device_fingerprint_android import generate_android_fingerprint
from core.emulator import EmulatorError
from core.repositories.device_fingerprint import record_second_device
from schemas.accounts import AccountCreate
from schemas.telegram_actions import ActionResult, SetOnline
from schemas.warming import StartWarmingRequest, StopWarmingRequest
from services import warming
from services.warming import _mobile_prep, _mobile_prep_state, _runner, _runtime, _seams
from services.warming._state import _set_state
from tests.services.warming._support import _fake_loop, _set_settings

if TYPE_CHECKING:
    from schemas.telegram_actions import TelegramAction


@pytest.fixture(autouse=True)
def prepared_phones(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Mobile set up on a capable host; the phone jobs a start launches are recorded."""
    monkeypatch.setattr(settings.telegram, "mobile_api_id", 6)
    monkeypatch.setattr(settings.telegram, "mobile_api_hash", "mobile-hash")
    monkeypatch.setattr(settings.emulator, "enabled", True)
    prepared: list[str] = []

    async def capable() -> None:
        return None

    async def prepare(account_id: str) -> None:
        prepared.append(account_id)

    monkeypatch.setattr(_mobile_prep, "check_host", capable)
    monkeypatch.setattr(_mobile_prep, "_prepare", prepare)
    return prepared


async def _account(*, mobile: bool) -> None:
    await create_account(AccountCreate(account_id="acc-1", session_name="acc-1"))
    if mobile:
        await record_second_device("acc-1_mobile", generate_android_fingerprint("acc-1", None))
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)


@pytest.mark.asyncio
async def test_start_defaults_to_desktop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_runtime, "_warming_loop", _fake_loop)
    await _account(mobile=True)

    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.device_mode == "desktop"


@pytest.mark.asyncio
async def test_the_chosen_mobile_device_is_persisted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_runtime, "_warming_loop", _fake_loop)
    await _account(mobile=True)

    card = await warming.start_warming(
        StartWarmingRequest(account_id="acc-1", device_mode="mobile"),
    )

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.device_mode == "mobile"
    assert card.devices == ["desktop", "mobile"]


@pytest.mark.asyncio
async def test_a_phone_stint_start_prepares_its_phone(
    monkeypatch: pytest.MonkeyPatch, prepared_phones: list[str]
) -> None:
    monkeypatch.setattr(_runtime, "_warming_loop", _fake_loop)
    await _account(mobile=True)

    await warming.start_warming(StartWarmingRequest(account_id="acc-1", device_mode="mobile"))
    await asyncio.gather(*_mobile_prep_state.JOBS.values())

    assert prepared_phones == ["acc-1"]


@pytest.mark.asyncio
async def test_stopping_a_phone_stint_cancels_its_phone_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_runtime, "_warming_loop", _fake_loop)
    await _account(mobile=True)
    preparing = asyncio.Event()

    async def prepare(_account_id: str) -> None:
        preparing.set()
        await asyncio.sleep(3600)  # still booting the pod

    monkeypatch.setattr(_mobile_prep, "_prepare", prepare)
    await warming.start_warming(StartWarmingRequest(account_id="acc-1", device_mode="mobile"))
    await preparing.wait()
    job = _mobile_prep_state.JOBS["acc-1"]

    card = await warming.stop_warming(StopWarmingRequest(account_id="acc-1"))
    await asyncio.wait({job}, timeout=5)

    assert job.cancelled(), "no phone is readied for a stint that ended"
    assert _mobile_prep_state.JOBS == {}
    assert card.phone_prep is None


@pytest.mark.asyncio
async def test_a_host_that_cannot_run_a_phone_refuses_the_start_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_runtime, "_warming_loop", _fake_loop)
    await _account(mobile=True)

    async def no_docker() -> None:
        code = "emulator_docker_unavailable"
        raise EmulatorError(code)

    monkeypatch.setattr(_mobile_prep, "check_host", no_docker)

    with pytest.raises(warming.WarmingDeviceUnavailableError) as refused:
        await warming.start_warming(StartWarmingRequest(account_id="acc-1", device_mode="mobile"))

    assert refused.value.code == "emulator_docker_unavailable"
    assert await fetch_warming_state("acc-1") is None


@pytest.mark.asyncio
async def test_a_device_the_account_lacks_is_refused_without_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_runtime, "_warming_loop", _fake_loop)
    await _account(mobile=False)

    with pytest.raises(warming.WarmingDeviceUnavailableError) as refused:
        await warming.start_warming(StartWarmingRequest(account_id="acc-1", device_mode="mobile"))

    assert refused.value.code == "device_missing"
    assert await fetch_warming_state("acc-1") is None, "nothing started, on any device"
    assert "acc-1" not in warming._RUNTIME


@pytest.mark.asyncio
async def test_mobile_without_its_app_credentials_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_runtime, "_warming_loop", _fake_loop)
    await _account(mobile=True)
    monkeypatch.setattr(settings.telegram, "mobile_api_id", 0)

    with pytest.raises(warming.WarmingDeviceUnavailableError) as refused:
        await warming.start_warming(StartWarmingRequest(account_id="acc-1", device_mode="mobile"))

    assert refused.value.code == "mobile_api_not_configured"


@pytest.mark.asyncio
async def test_mobile_with_the_emulator_off_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A phone stint runs only on the emulator: no silent Telethon stand-in."""
    monkeypatch.setattr(_runtime, "_warming_loop", _fake_loop)
    await _account(mobile=True)
    monkeypatch.setattr(settings.emulator, "enabled", False)

    with pytest.raises(warming.WarmingDeviceUnavailableError) as refused:
        await warming.start_warming(StartWarmingRequest(account_id="acc-1", device_mode="mobile"))

    assert refused.value.code == "mobile_emulator_disabled"
    assert await fetch_warming_state("acc-1") is None


@pytest.mark.asyncio
async def test_the_card_flags_a_mobile_stint_while_the_emulator_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _account(mobile=True)
    await _set_state("acc-1", "sleeping", device_mode="mobile")
    monkeypatch.setattr(settings.emulator, "enabled", False)

    board = await warming.load_board()

    assert [card.mobile_emulator_off for card in board.warming] == [True]
    monkeypatch.setattr(settings.emulator, "enabled", True)
    assert [card.mobile_emulator_off for card in (await warming.load_board()).warming] == [False]


@pytest.mark.asyncio
async def test_restarting_a_running_stint_keeps_its_device(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_runtime, "_warming_loop", _fake_loop)
    await _account(mobile=True)
    await warming.start_warming(StartWarmingRequest(account_id="acc-1", device_mode="mobile"))

    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.device_mode == "mobile", "a carried stint keeps the device it started on"


@pytest.mark.asyncio
async def test_unrelated_state_writes_leave_the_device_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_runtime, "_warming_loop", _fake_loop)
    await _account(mobile=True)
    await warming.start_warming(StartWarmingRequest(account_id="acc-1", device_mode="mobile"))

    await _set_state("acc-1", "sleeping", last_event="cycle_done")

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.device_mode == "mobile"


@pytest.mark.asyncio
async def test_a_resumed_loop_dispatches_through_the_persisted_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After a backend restart the loop reads its device back from the stint's row."""
    await _account(mobile=True)
    await _set_state("acc-1", "active", run_id="run-1", device_mode="mobile")
    _seams.activate_lease("acc-1", "run-1")
    seen: list[str | None] = []

    async def gateway(
        account_id: str,
        action: TelegramAction,
        *,
        domain: str,
        device_type: str | None = None,
    ) -> ActionResult:
        assert domain == "warming"
        seen.append(device_type)
        return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)

    async def one_iteration(account_id: str, *, run_id: str | None = None) -> None:
        await _seams.execute(account_id, SetOnline(online=True))
        await _set_state(account_id, "idle", run_id=None, expected_run_id=run_id)

    async def no_delay(*_args: object) -> float:
        return 0.0

    monkeypatch.setattr(_seams, "_gateway_execute", gateway)
    monkeypatch.setattr(_runner, "run_loop_iteration", one_iteration)
    monkeypatch.setattr(_runner, "_initial_delay_seconds", no_delay)

    await asyncio.wait_for(_runner._warming_loop("acc-1", run_id="run-1"), timeout=5)

    assert seen == ["mobile"]


@pytest.mark.asyncio
async def test_a_direct_cycle_borrows_the_primary_device(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, object]] = []

    async def gateway(account_id: str, action: TelegramAction, **kwargs: object) -> ActionResult:
        seen.append(kwargs)
        return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)

    monkeypatch.setattr(_seams, "_gateway_execute", gateway)

    await _seams.execute("acc-1", SetOnline(online=True))

    assert seen == [{"domain": "warming", "device_type": None}]
