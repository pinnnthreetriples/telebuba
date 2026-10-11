"""Service-layer tests for Zero-Click Mobile Emulator (services/accounts/mobile_emulator.py)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import (
    configure_database,
    set_account_twofa_password,
    upsert_device_fingerprint,
)
from core.emulator.ldconsole import EmulatorNotFoundError
from core.emulator.session_capture import LoginCodeTimeoutError
from schemas.accounts import AccountCreate
from schemas.device_fingerprint import DeviceFingerprint
from services.accounts.lifecycle import add_account
from services.accounts.mobile_emulator import (
    MobileEmulatorLaunchError,
    MobileEmulatorNotFoundError,
    MobileLoginCodeTimeoutError,
    NoProxyForMobileError,
    _reset_mobile_emulator_for_tests,
    open_account_mobile,
    shutdown_mobile_emulator,
)
from tests.factories import seed_account_proxy

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: Path) -> Iterator[None]:
    configure_database(tmp_path / "mobile_emulator_svc.db")
    _reset_mobile_emulator_for_tests()
    yield
    _reset_mobile_emulator_for_tests()


class _FakeRelay:
    def __init__(self, _proxy: object) -> None:
        self.port = 19090
        self.closed = False

    async def start(self) -> int:
        return self.port

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_open_account_mobile_refuses_when_no_proxy_assigned() -> None:
    await add_account(
        AccountCreate(
            account_id="acc-no-proxy",
            phone="+79991112233",
            device_profiles=["desktop", "mobile"],
        )
    )

    with pytest.raises(NoProxyForMobileError, match="mobile_emulator_no_proxy"):
        await open_account_mobile("acc-no-proxy")


@pytest.mark.asyncio
async def test_open_account_mobile_refuses_when_no_mobile_profile() -> None:
    await add_account(
        AccountCreate(
            account_id="acc-desktop-only",
            phone="+79991112244",
            device_profiles=["desktop"],
        )
    )
    await seed_account_proxy("acc-desktop-only")

    with pytest.raises(MobileEmulatorLaunchError, match="mobile_emulator_no_profile"):
        await open_account_mobile("acc-desktop-only")


def _install_emulator_fakes(  # noqa: C901
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[tuple[str, object]], list[str]]:
    actions: list[tuple[str, object]] = []
    typed_texts: list[str] = []
    running_state = {"running": False}

    async def fake_is_running(_name: str) -> bool:
        return running_state["running"]

    async def fake_create(name: str) -> None:
        actions.append(("create", name))

    async def fake_set_profile(
        name: str,
        *,
        model: str,
        imei: str,
        android_id: str,
        mac: str,
    ) -> None:
        actions.append(("profile", (name, model, len(imei), len(android_id), len(mac))))

    async def fake_set_resolution(
        name: str,
        *,
        width: int = 412,
        height: int = 915,
        dpi: int = 320,
    ) -> None:
        actions.append(("resolution", (name, width, height, dpi)))

    async def fake_set_resources(name: str, *, cpus: int = 1, ram_mb: int = 1536) -> None:
        actions.append(("resources", (name, cpus, ram_mb)))

    async def fake_launch(name: str) -> None:
        running_state["running"] = True
        actions.append(("launch", name))

    async def fake_stop(name: str) -> None:
        running_state["running"] = False
        actions.append(("stop", name))

    async def fake_wait_boot(serial: str) -> None:
        actions.append(("wait_boot", serial))

    async def fake_cfg_proxy(serial: str, host: str, port: int) -> None:
        actions.append(("proxy", (serial, host, port)))

    async def fake_start_app(serial: str) -> None:
        actions.append(("start_app", serial))

    async def fake_input_text(serial: str, text: str) -> None:
        typed_texts.append(text)
        actions.append(("input_text", (serial, text)))

    async def fake_press_key(serial: str, keycode: int) -> None:
        actions.append(("press_key", (serial, keycode)))

    async def fake_wait_code(account_id: str) -> str:
        actions.append(("wait_code", account_id))
        return "54321"

    monkeypatch.setattr("services.accounts.mobile_emulator.LocalProxyRelay", _FakeRelay)
    monkeypatch.setattr("services.accounts.mobile_emulator.is_running", fake_is_running)
    monkeypatch.setattr("services.accounts.mobile_emulator.create_instance", fake_create)
    monkeypatch.setattr("services.accounts.mobile_emulator.set_device_profile", fake_set_profile)
    monkeypatch.setattr("services.accounts.mobile_emulator.set_resolution", fake_set_resolution)
    monkeypatch.setattr(
        "services.accounts.mobile_emulator.set_resource_limits",
        fake_set_resources,
    )
    monkeypatch.setattr("services.accounts.mobile_emulator.launch_instance", fake_launch)
    monkeypatch.setattr("services.accounts.mobile_emulator.stop_instance", fake_stop)
    monkeypatch.setattr("services.accounts.mobile_emulator.wait_for_boot", fake_wait_boot)
    monkeypatch.setattr("services.accounts.mobile_emulator.configure_http_proxy", fake_cfg_proxy)
    monkeypatch.setattr("services.accounts.mobile_emulator.start_app", fake_start_app)
    monkeypatch.setattr("services.accounts.mobile_emulator.input_text", fake_input_text)
    monkeypatch.setattr("services.accounts.mobile_emulator.press_key", fake_press_key)
    monkeypatch.setattr(
        "services.accounts.mobile_emulator.wait_for_telegram_login_code",
        fake_wait_code,
    )
    return actions, typed_texts


@pytest.mark.asyncio
async def test_open_account_mobile_zero_click_flow_with_777000_code_and_2fa(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await add_account(
        AccountCreate(
            account_id="acc-zero-click",
            phone="+79995556677",
            device_profiles=["desktop", "mobile"],
        )
    )
    await seed_account_proxy("acc-zero-click")
    await set_account_twofa_password("acc-zero-click", "secret2fa")
    await upsert_device_fingerprint(
        DeviceFingerprint(
            account_id="acc-zero-click",
            device_type="mobile",
            platform="android",
            device_model="Samsung Galaxy S24 Ultra",
            system_version="SDK 34 (Android 14)",
            app_version="Telegram Android 10.9.2",
            lang_code="ru",
            system_lang_code="ru-RU",
        )
    )

    actions, typed_texts = _install_emulator_fakes(monkeypatch)

    result = await open_account_mobile("acc-zero-click")
    assert result.launched is True
    assert result.signed_in is True
    assert result.instance_name == "telebuba_acc-zero-click"

    assert "+79995556677" in typed_texts
    assert "54321" in typed_texts
    assert "secret2fa" in typed_texts

    # Second call while running reuses the running instance without re-entering code.
    actions.clear()
    second = await open_account_mobile("acc-zero-click")
    assert second.launched is True
    assert second.signed_in is True
    assert ("wait_code", "acc-zero-click") not in actions

    # Shutdown stops the running instance and cleans up relays.
    await shutdown_mobile_emulator()
    assert ("stop", "telebuba_acc-zero-click") in actions


@pytest.mark.asyncio
async def test_open_account_mobile_maps_missing_binary_and_code_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await add_account(
        AccountCreate(
            account_id="acc-err",
            phone="+79998887766",
            device_profiles=["desktop", "mobile"],
        )
    )
    await seed_account_proxy("acc-err")

    async def fail_not_found(_name: str) -> bool:
        msg = "ldconsole not found"
        raise EmulatorNotFoundError(msg)

    monkeypatch.setattr("services.accounts.mobile_emulator.is_running", fail_not_found)

    with pytest.raises(MobileEmulatorNotFoundError, match="mobile_emulator_not_found"):
        await open_account_mobile("acc-err")

    async def not_running(_name: str) -> bool:
        return False

    async def noop(*_args: object, **_kwargs: object) -> None:
        return None

    async def timeout_code(_account_id: str) -> str:
        msg = "timed out waiting for 777000"
        raise LoginCodeTimeoutError(msg)

    monkeypatch.setattr("services.accounts.mobile_emulator.LocalProxyRelay", _FakeRelay)
    monkeypatch.setattr("services.accounts.mobile_emulator.is_running", not_running)
    monkeypatch.setattr("services.accounts.mobile_emulator.create_instance", noop)
    monkeypatch.setattr("services.accounts.mobile_emulator.set_device_profile", noop)
    monkeypatch.setattr("services.accounts.mobile_emulator.set_resolution", noop)
    monkeypatch.setattr("services.accounts.mobile_emulator.set_resource_limits", noop)
    monkeypatch.setattr("services.accounts.mobile_emulator.launch_instance", noop)
    monkeypatch.setattr("services.accounts.mobile_emulator.stop_instance", noop)
    monkeypatch.setattr("services.accounts.mobile_emulator.wait_for_boot", noop)
    monkeypatch.setattr("services.accounts.mobile_emulator.configure_http_proxy", noop)
    monkeypatch.setattr("services.accounts.mobile_emulator.start_app", noop)
    monkeypatch.setattr("services.accounts.mobile_emulator.input_text", noop)
    monkeypatch.setattr("services.accounts.mobile_emulator.press_key", noop)
    monkeypatch.setattr(
        "services.accounts.mobile_emulator.wait_for_telegram_login_code",
        timeout_code,
    )

    with pytest.raises(MobileLoginCodeTimeoutError, match="mobile_emulator_code_timeout"):
        await open_account_mobile("acc-err")
