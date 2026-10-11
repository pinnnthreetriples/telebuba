"""Unit tests for core/emulator (ldconsole, adb, session_capture, config)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from core.emulator.adb import (
    KEYCODE_ENTER,
    clear_http_proxy,
    configure_http_proxy,
    dump_ui_xml,
    input_text,
    press_key,
    start_app,
    tap,
    wait_for_boot,
    wait_for_text,
)
from core.emulator.config import EmulatorSettings
from core.emulator.ldconsole import (
    EmulatorCommandError,
    EmulatorNotFoundError,
    create_instance,
    deterministic_hardware_ids,
    is_running,
    launch_instance,
    set_device_profile,
    set_resolution,
    set_resource_limits,
    stop_instance,
)
from core.emulator.session_capture import (
    LoginCodeTimeoutError,
    extract_login_code,
    wait_for_telegram_login_code,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_emulator_settings_defaults() -> None:
    cfg = EmulatorSettings()
    assert cfg.screen_width == 412
    assert cfg.screen_height == 915
    assert cfg.screen_dpi == 320
    assert cfg.cpus == 1
    assert cfg.ram_mb == 1536
    assert cfg.login_code_timeout_seconds == 45.0


def test_deterministic_hardware_ids_are_stable_per_account() -> None:
    imei1, aid1, mac1 = deterministic_hardware_ids("acc-101")
    imei2, aid2, mac2 = deterministic_hardware_ids("acc-101")
    imei_other, aid_other, mac_other = deterministic_hardware_ids("acc-202")

    assert (imei1, aid1, mac1) == (imei2, aid2, mac2)
    assert len(imei1) == 15
    assert imei1.isdigit()
    assert len(aid1) == 16
    assert len(mac1.split(":")) == 6
    assert (imei1, aid1, mac1) != (imei_other, aid_other, mac_other)


@pytest.mark.asyncio
async def test_ldconsole_commands_build_expected_argv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_ld = tmp_path / "ldconsole.exe"
    fake_ld.write_bytes(b"MZ")
    cfg = EmulatorSettings(ldconsole_path=fake_ld)

    recorded: list[list[str]] = []

    async def fake_run(
        argv: list[str],
        *,
        timeout: float = 30.0,  # noqa: ARG001, ASYNC109
    ) -> tuple[int, str, str]:
        recorded.append(argv)
        if argv[1] == "isrunning":
            return 0, "running", ""
        return 0, "ok", ""

    monkeypatch.setattr("core.emulator.ldconsole._run_subprocess", fake_run)

    await create_instance("telebuba_acc1", cfg=cfg)
    await set_device_profile(
        "telebuba_acc1",
        model="Samsung Galaxy S24 Ultra",
        imei="351234567890123",
        android_id="a1b2c3d4e5f60718",
        mac="02:11:22:33:44:55",
        cfg=cfg,
    )
    await set_resolution("telebuba_acc1", width=412, height=915, dpi=320, cfg=cfg)
    await set_resource_limits("telebuba_acc1", cpus=1, ram_mb=1536, cfg=cfg)
    await launch_instance("telebuba_acc1", cfg=cfg)
    assert await is_running("telebuba_acc1", cfg=cfg) is True
    await stop_instance("telebuba_acc1", cfg=cfg)

    verbs = [cmd[1] for cmd in recorded]
    assert verbs == ["add", "modify", "modify", "modify", "launch", "isrunning", "quit"]
    assert "412,915,320" in recorded[2]


@pytest.mark.asyncio
async def test_ldconsole_raises_when_binary_missing(tmp_path: Path) -> None:
    cfg = EmulatorSettings(ldconsole_path=tmp_path / "missing_ldconsole.exe")
    with pytest.raises(EmulatorNotFoundError):
        await is_running("telebuba_acc1", cfg=cfg)


@pytest.mark.asyncio
async def test_ldconsole_raises_on_nonzero_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_ld = tmp_path / "ldconsole.exe"
    fake_ld.write_bytes(b"MZ")
    cfg = EmulatorSettings(ldconsole_path=fake_ld)

    async def fake_run(
        _argv: list[str],
        *,
        timeout: float = 30.0,  # noqa: ARG001, ASYNC109
    ) -> tuple[int, str, str]:
        return 1, "", "failed"

    monkeypatch.setattr("core.emulator.ldconsole._run_subprocess", fake_run)
    with pytest.raises(EmulatorCommandError):
        await launch_instance("telebuba_acc1", cfg=cfg)


@pytest.mark.asyncio
async def test_adb_commands_execute_expected_shell_actions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_adb = tmp_path / "adb.exe"
    fake_adb.write_bytes(b"MZ")
    cfg = EmulatorSettings(adb_path=fake_adb)

    recorded: list[list[str]] = []

    async def fake_run(
        argv: list[str],
        *,
        timeout: float = 30.0,  # noqa: ARG001, ASYNC109
    ) -> tuple[int, str, str]:
        recorded.append(argv)
        if "sys.boot_completed" in argv:
            return 0, "1\n", ""
        if "uiautomator" in argv:
            return 0, "<hierarchy><node text='Start Messaging' /></hierarchy>", ""
        return 0, "", ""

    monkeypatch.setattr("core.emulator.adb._run_subprocess", fake_run)

    await wait_for_boot("emulator-5554", timeout=5.0, cfg=cfg)
    await configure_http_proxy("emulator-5554", "10.0.2.2", 18080, cfg=cfg)
    await start_app("emulator-5554", cfg=cfg)
    await tap("emulator-5554", 206, 780, cfg=cfg)
    await input_text("emulator-5554", "+79991234567", cfg=cfg)
    await press_key("emulator-5554", KEYCODE_ENTER, cfg=cfg)
    xml = await dump_ui_xml("emulator-5554", cfg=cfg)
    assert "Start Messaging" in xml
    found = await wait_for_text("emulator-5554", "Start Messaging", timeout=2.0, cfg=cfg)
    assert found is True
    await clear_http_proxy("emulator-5554", cfg=cfg)

    joined = [" ".join(cmd[1:]) for cmd in recorded]
    assert any("settings put global http_proxy 10.0.2.2:18080" in c for c in joined)
    assert any(
        "am start -n org.telegram.messenger/org.telegram.ui.LaunchActivity" in c for c in joined
    )
    assert any("input tap 206 780" in c for c in joined)
    assert any("input text +79991234567" in c for c in joined)
    assert any("input keyevent 66" in c for c in joined)
    assert any("settings put global http_proxy :0" in c for c in joined)


def test_extract_login_code_parses_five_digit_service_code() -> None:
    msg = (
        "Login code: 54321. Do not give this code to anyone, even if they say "
        "they are from Telegram!"
    )
    assert extract_login_code(msg) == "54321"
    assert extract_login_code("No digits here") is None


@pytest.mark.asyncio
async def test_wait_for_telegram_login_code_intercepts_777000_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)

    class _FakeClient:
        async def get_messages(self, peer: int, limit: int = 5) -> list[object]:  # noqa: ARG002
            assert peer == 777000
            return [
                SimpleNamespace(
                    message="Login code: 89123. Do not share it.",
                    date=now,
                )
            ]

    async def fake_get_client(account_id: str, device_type: str = "desktop") -> object:
        assert account_id == "acc-code"
        assert device_type == "desktop"
        return _FakeClient()

    monkeypatch.setattr("core.emulator.session_capture.get_client", fake_get_client)

    code = await wait_for_telegram_login_code("acc-code", timeout=2.0, poll_interval=0.05)
    assert code == "89123"


@pytest.mark.asyncio
async def test_wait_for_telegram_login_code_times_out_when_no_code_arrives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _EmptyClient:
        async def get_messages(self, peer: int, limit: int = 5) -> list[object]:  # noqa: ARG002
            return []

    async def fake_get_client(_account_id: str, device_type: str = "desktop") -> object:  # noqa: ARG001
        return _EmptyClient()

    monkeypatch.setattr("core.emulator.session_capture.get_client", fake_get_client)

    with pytest.raises(LoginCodeTimeoutError):
        await wait_for_telegram_login_code("acc-timeout", timeout=0.1, poll_interval=0.02)
