"""With the emulator on, a new mobile device also signs the official app in, in its pod.

The Telethon second-device sign-in and the pod are both fakes: what is pinned is the
order (the device is recorded first), the pacing stamp handed to the app's own code
request, and that nothing but a mobile device with the emulator on reaches a pod.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import fetch_account
from schemas.accounts import AccountCreate
from schemas.devices import SecondDeviceResult, SecondDeviceSignIn
from schemas.emulator import OpenMobileResult
from services.accounts import add_account, add_account_device
from services.accounts import devices as devices_module
from services.accounts import mobile_emulator as emulator_module
from services.accounts.mobile_emulator import EmulatorRefusedError
from tests.services.accounts.test_devices import _fake_sign_in

if TYPE_CHECKING:
    from schemas.device_fingerprint import DeviceType


class _Pod:
    def __init__(self) -> None:
        self.opened: list[str] = []
        # The pacing stamp each open found: ``None`` means the app may ask for a code.
        self.stamps: list[float | None] = []
        self.error: EmulatorRefusedError | None = None

    async def open(self, account_id: str) -> OpenMobileResult:
        self.opened.append(account_id)
        self.stamps.append(devices_module._LAST_CODE_REQUEST.get(account_id))
        if self.error is not None:
            raise self.error
        return OpenMobileResult(account_id=account_id, status="started", signed_in=True)


@pytest.fixture
def pod(monkeypatch: pytest.MonkeyPatch) -> _Pod:
    fake = _Pod()
    monkeypatch.setattr(settings.telegram, "mobile_api_id", 6)
    monkeypatch.setattr(settings.telegram, "mobile_api_hash", "mobile-hash")
    monkeypatch.setattr(settings.emulator, "enabled", True)
    monkeypatch.setattr(emulator_module, "open_account_mobile", fake.open)
    devices_module._LAST_CODE_REQUEST.clear()
    return fake


async def _account(primary: DeviceType = "desktop") -> None:
    await add_account(
        AccountCreate(
            account_id="acc", session_name="acc", phone="+5215550001111", session_device=primary
        ),
    )


@pytest.mark.asyncio
async def test_a_mobile_device_is_signed_in_and_then_opened_in_its_pod(
    pod: _Pod, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _account()
    calls = _fake_sign_in(monkeypatch)

    account = await add_account_device("acc", "mobile")

    assert [c.device_type for c in calls] == ["mobile"]
    assert pod.opened == ["acc"]
    assert pod.stamps == [None], "the app's own code request is not refused as a repeat"
    assert account.devices == ["desktop", "mobile"]


@pytest.mark.asyncio
async def test_a_pod_that_fails_answers_its_code_and_the_device_stays(
    pod: _Pod, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _account()
    _fake_sign_in(monkeypatch)
    pod.error = EmulatorRefusedError("emulator_proxy_missing")

    with pytest.raises(EmulatorRefusedError) as refused:
        await add_account_device("acc", "mobile")

    assert refused.value.code == "emulator_proxy_missing"
    account = await fetch_account("acc")
    assert account is not None
    assert account.devices == ["desktop", "mobile"], "opened again later from the account"


@pytest.mark.asyncio
async def test_a_refused_sign_in_never_reaches_a_pod(
    pod: _Pod, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _account()
    _fake_sign_in(monkeypatch, code="device_code_timeout")

    with pytest.raises(devices_module.DeviceRefusedError):
        await add_account_device("acc", "mobile")

    assert pod.opened == []


@pytest.mark.asyncio
async def test_the_emulator_off_keeps_the_telethon_only_device(
    pod: _Pod, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.emulator, "enabled", False)
    await _account()
    _fake_sign_in(monkeypatch)

    account = await add_account_device("acc", "mobile")

    assert account.devices == ["desktop", "mobile"]
    assert pod.opened == []


@pytest.mark.asyncio
async def test_a_desktop_device_never_reaches_a_pod(
    pod: _Pod, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _account(primary="mobile")
    calls: list[DeviceType] = []

    async def signed_in(request: SecondDeviceSignIn) -> SecondDeviceResult:
        calls.append(request.device_type)
        return SecondDeviceResult(account_id=request.account_id, device_type=request.device_type)

    monkeypatch.setattr(devices_module, "authorize_second_device", signed_in)

    await add_account_device("acc", "desktop")

    assert calls == ["desktop"]
    assert pod.opened == []
