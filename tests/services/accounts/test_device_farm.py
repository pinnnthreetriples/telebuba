"""The device farm's phones: listed with a status, started in the background, put to sleep.

The container listing and the account's open/stop are fakes on ``_phones``' own
names; the accounts, proxies and mobile fingerprints are real rows.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.device_fingerprint_android import android_device_for
from core.emulator import EmulatorError
from core.emulator.redroid import Instance
from schemas.accounts import AccountCreate
from services.accounts import add_account
from services.accounts import mobile_emulator as emulator_module
from services.accounts.mobile_emulator import EmulatorBusyError, EmulatorRefusedError
from services.device_farm import _phones, list_phones, start_phone, stop_phone
from tests.services.accounts.test_mobile_emulator import _account, _enabled  # noqa: F401

if TYPE_CHECKING:
    from schemas.emulator import EmulatorRefusalCode


class _Farm:
    def __init__(self) -> None:
        self.instances: list[Instance] = []
        self.fail: EmulatorRefusalCode | None = None
        self.opened: list[str] = []
        self.stopped: list[str] = []
        self.open_gate = asyncio.Event()

    async def list_instances(self) -> list[Instance]:
        if self.fail is not None:
            raise EmulatorError(self.fail)
        return self.instances

    async def open_account_mobile(self, account_id: str) -> None:
        self.opened.append(account_id)
        await self.open_gate.wait()

    async def stop_account_pod(self, account_id: str) -> None:
        self.stopped.append(account_id)


@pytest.fixture
def farm(monkeypatch: pytest.MonkeyPatch) -> _Farm:
    fake = _Farm()
    for name in ("list_instances", "open_account_mobile", "stop_account_pod"):
        monkeypatch.setattr(_phones, name, getattr(fake, name))
    emulator_module._ACTIVE.clear()
    emulator_module._MANUAL.clear()
    return fake


@pytest.mark.asyncio
async def test_a_mobile_account_is_a_sleeping_phone_until_its_pod_runs(farm: _Farm) -> None:
    await _account()

    asleep = await list_phones()
    farm.instances = [Instance(1, "telebuba-acc", running=True)]
    running = await list_phones()

    assert asleep.enabled
    assert asleep.problem is None
    [phone] = asleep.phones
    assert phone.phone_id == "telebuba-acc"
    assert (phone.created, phone.status) == (False, "asleep")
    device = android_device_for(phone.device_model)
    assert device is not None
    assert phone.model_name == device.name
    assert phone.maker[0].isupper()
    assert phone.maker.casefold() == device.manufacturer.casefold()
    assert phone.apps[0].platform == "telegram"
    assert phone.apps[0].account_id == "acc"
    assert phone.proxy is not None
    assert (phone.proxy.host, phone.proxy.port, phone.proxy.proxy_type) == (
        "10.0.0.5",
        1080,
        "socks5",
    )
    assert set(phone.proxy.model_dump()) == {"proxy_type", "host", "port", "country_code", "status"}
    assert (running.phones[0].created, running.phones[0].status) == (True, "free")


@pytest.mark.asyncio
async def test_what_holds_the_phone_wins_over_its_container(farm: _Farm) -> None:
    await _account()
    farm.instances = [Instance(1, "telebuba-acc", running=True)]

    emulator_module._ACTIVE.add("acc")
    warming = (await list_phones()).phones[0].status
    emulator_module._ACTIVE.clear()
    emulator_module._MANUAL.add("acc")
    manual = (await list_phones()).phones[0].status

    assert (warming, manual) == ("warming", "manual")


@pytest.mark.usefixtures("farm")
@pytest.mark.asyncio
async def test_a_desktop_only_account_is_no_phone() -> None:
    await add_account(AccountCreate(account_id="desk", session_name="desk", phone="+15550001"))

    assert (await list_phones()).phones == []


@pytest.mark.asyncio
async def test_a_switched_off_emulator_lists_without_asking_docker(
    farm: _Farm, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _account()
    monkeypatch.setattr(settings.emulator, "enabled", False)
    farm.fail = "emulator_not_found"  # would raise if docker were asked

    listed = await list_phones()

    assert (listed.enabled, listed.problem) == (False, "mobile_emulator_disabled")
    assert listed.phones[0].status == "asleep"


@pytest.mark.asyncio
async def test_docker_not_answering_is_reported_not_raised(farm: _Farm) -> None:
    await _account()
    farm.fail = "emulator_command_timeout"

    listed = await list_phones()

    assert (listed.enabled, listed.problem) == (False, "emulator_command_timeout")
    assert len(listed.phones) == 1


@pytest.mark.asyncio
async def test_a_start_answers_at_once_and_opens_in_the_background(farm: _Farm) -> None:
    await _account()

    result = await start_phone("telebuba-acc")

    assert result.status == "starting"
    assert farm.opened == ["acc"]
    farm.open_gate.set()
    await asyncio.gather(*_phones._STARTS)


@pytest.mark.usefixtures("farm")
@pytest.mark.asyncio
async def test_a_failed_background_start_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    await _account()

    async def broken(_account_id: str) -> None:
        code: EmulatorRefusalCode = "emulator_boot_timeout"
        raise EmulatorRefusedError(code)

    monkeypatch.setattr(_phones, "open_account_mobile", broken)

    await start_phone("telebuba-acc")
    await asyncio.gather(*_phones._STARTS)  # nothing raised out of the task


@pytest.mark.parametrize("phone_id", ["acc", "telebuba-", "other-acc"])
@pytest.mark.asyncio
async def test_an_id_that_is_no_phone_is_not_found(farm: _Farm, phone_id: str) -> None:
    with pytest.raises(EmulatorRefusedError) as refused:
        await start_phone(phone_id)

    assert refused.value.code == "emulator_not_found"
    assert farm.opened == []


@pytest.mark.asyncio
async def test_a_start_is_refused_while_switched_off_or_held(
    farm: _Farm, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _account()
    emulator_module._MANUAL.add("acc")
    with pytest.raises(EmulatorBusyError):
        await start_phone("telebuba-acc")
    monkeypatch.setattr(settings.emulator, "enabled", False)
    with pytest.raises(EmulatorRefusedError) as refused:
        await start_phone("telebuba-acc")

    assert refused.value.code == "mobile_emulator_disabled"
    assert farm.opened == []


@pytest.mark.asyncio
async def test_stop_puts_a_free_or_manual_phone_to_sleep(farm: _Farm) -> None:
    await _account()
    emulator_module._MANUAL.add("acc")

    result = await stop_phone("telebuba-acc")

    assert result.status == "asleep"
    assert farm.stopped == ["acc"]


@pytest.mark.asyncio
async def test_stop_leaves_a_warming_phone_to_the_warming_board(farm: _Farm) -> None:
    await _account()
    emulator_module._ACTIVE.add("acc")

    with pytest.raises(EmulatorBusyError):
        await stop_phone("telebuba-acc")

    assert farm.stopped == []


def test_a_phone_outside_the_catalogue_shows_its_raw_model() -> None:
    assert _phones._names("SomeVendorX1") == ("", "SomeVendorX1")
