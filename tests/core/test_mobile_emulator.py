"""Unit tests for core Android emulator subsystem: CLI, ADB, and 777000 extraction."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from core.emulator.adb import (
    KEYCODE_ENTER,
    ADBDriver,
    parse_element_center,
)
from core.emulator.config import (
    EmulatorLaunchError,
    EmulatorNotFoundError,
    LoginCodeTimeoutError,
    generate_device_profile,
)
from core.emulator.ldconsole import LDConsoleRunner
from core.emulator.session_capture import (
    extract_verification_code,
    poll_for_login_code,
)


class TestSessionCapture:
    """Verification code regex boundary and extraction tests."""

    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            ("Login code: 54321. Do not give this code to anyone.", "54321"),
            ("Login code 98765", "98765"),
            ("Telegram login code: 12345", "12345"),
            ("Your code is 67890. Keep it secret.", "67890"),
            ("Код подтверждения: 34567. Никому не сообщайте.", "34567"),
            ("Код для входа: 89012", "89012"),
            ("Ваш код: 45678", "45678"),
            ("Код 11223", "11223"),
            ("Notification: use 99887 to log in", "99887"),
            ("Your verification code is 00123", "00123"),
        ],
    )
    def test_extracts_valid_five_digit_codes(self, message: str, expected: str) -> None:
        assert extract_verification_code(message) == expected

    @pytest.mark.parametrize(
        "message",
        [
            "",
            "No code here",
            "Your 4-digit PIN is 1234",
            "Order #123456 has been shipped",
            "Call 18005550199 for assistance",
            "Token ABC12345DEF",
            "Too short: 999",
        ],
    )
    def test_rejects_invalid_or_non_five_digit_numbers(self, message: str) -> None:
        assert extract_verification_code(message) is None

    @pytest.mark.asyncio
    async def test_poll_for_login_code_finds_recent_message(self) -> None:
        mock_msg = MagicMock()
        mock_msg.date = datetime(2026, 10, 11, 12, 0, 0, tzinfo=UTC)
        mock_msg.raw_text = "Login code: 43210. Do not give it to anyone."

        mock_client = AsyncMock()
        mock_client.get_messages.return_value = [mock_msg]

        code = await poll_for_login_code(
            account_id="acc-1",
            since_timestamp=1760184000.0,
            timeout_seconds=2.0,
            poll_interval=0.1,
            client=mock_client,
        )
        assert code == "43210"

    @pytest.mark.asyncio
    async def test_poll_for_login_code_times_out(self) -> None:
        mock_client = AsyncMock()
        mock_client.get_messages.return_value = []

        with pytest.raises(LoginCodeTimeoutError, match="No login code received"):
            await poll_for_login_code(
                account_id="acc-1",
                timeout_seconds=0.2,
                poll_interval=0.05,
                client=mock_client,
            )


class TestLDConsoleRunner:
    """Tests for ldconsole.exe CLI execution."""

    @pytest.mark.asyncio
    async def test_raises_when_binary_not_found(self, tmp_path: Path) -> None:
        runner = LDConsoleRunner(executable_path=tmp_path / "nonexistent.exe")
        with pytest.raises(EmulatorNotFoundError):
            await runner.is_running("test_instance")

    @pytest.mark.asyncio
    async def test_exec_handles_non_zero_exit_code(self, tmp_path: Path) -> None:
        fake_exe = tmp_path / "ldconsole.exe"
        fake_exe.write_text("dummy", encoding="utf-8")

        runner = LDConsoleRunner(executable_path=fake_exe)
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            proc = AsyncMock()
            proc.communicate.return_value = (b"", b"Instance failed to launch")
            proc.returncode = 1
            mock_exec.return_value = proc

            with pytest.raises(EmulatorLaunchError, match="Instance failed to launch"):
                await runner._exec("launch", "--name", "inst1")

    @pytest.mark.asyncio
    async def test_is_running_parses_status(self, tmp_path: Path) -> None:
        fake_exe = tmp_path / "ldconsole.exe"
        fake_exe.write_text("dummy", encoding="utf-8")

        runner = LDConsoleRunner(executable_path=fake_exe)
        with patch.object(runner, "_exec", return_value="running"):
            assert await runner.is_running("inst1") is True

        with patch.object(runner, "_exec", return_value="stop"):
            assert await runner.is_running("inst1") is False

    @pytest.mark.asyncio
    async def test_instance_configuration_commands(self, tmp_path: Path) -> None:
        fake_exe = tmp_path / "ldconsole.exe"
        fake_exe.write_text("dummy", encoding="utf-8")

        runner = LDConsoleRunner(executable_path=fake_exe)
        with patch.object(runner, "_exec", return_value="") as mock_exec:
            await runner.create_instance("inst1")
            mock_exec.assert_called_with("add", "--name", "inst1")

            await runner.set_device_profile(
                "inst1",
                model="Samsung S23",
                imei="860000000000001",
                android_id="abcd1234abcd1234",
                mac="00:11:22:33:44:55",
            )
            mock_exec.assert_called_with(
                "modify",
                "--name",
                "inst1",
                "--model",
                "Samsung S23",
                "--imei",
                "860000000000001",
                "--androidid",
                "abcd1234abcd1234",
                "--mac",
                "00:11:22:33:44:55",
            )

            await runner.set_resolution("inst1", width=412, height=915, dpi=320)
            mock_exec.assert_called_with(
                "modify",
                "--name",
                "inst1",
                "--resolution",
                "412,915,320",
            )

            await runner.set_resource_limits("inst1", cpus=1, ram_mb=1536)
            mock_exec.assert_called_with(
                "modify",
                "--name",
                "inst1",
                "--cpu",
                "1",
                "--memory",
                "1536",
            )

            await runner.launch_instance("inst1")
            mock_exec.assert_called_with("launch", "--name", "inst1")

            await runner.stop_instance("inst1")
            mock_exec.assert_called_with("quit", "--name", "inst1")


class TestADBDriver:
    """Tests for ADB driver actions and XML parsing."""

    def test_parse_element_center(self) -> None:
        xml = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<hierarchy rotation="0">'
            '<node text="Start Messaging" bounds="[100,200][300,400]"/>'
            '<node content-desc="Next" bounds="[10,20][50,60]"/>'
            "</hierarchy>"
        )
        assert parse_element_center(xml, "Start Messaging") == (200, 300)
        assert parse_element_center(xml, "Next") == (30, 40)
        assert parse_element_center(xml, "Nonexistent") is None

    @pytest.mark.asyncio
    async def test_adb_driver_actions(self) -> None:
        runner = MagicMock()
        runner.run_adb = AsyncMock(return_value="")

        adb = ADBDriver("inst1", runner=runner)

        await adb.tap(150, 250)
        runner.run_adb.assert_called_with("inst1", "input tap 150 250")

        await adb.input_text("Hello World & Co")
        runner.run_adb.assert_called_with("inst1", "input text Hello%sWorld%s\\&%sCo")

        await adb.press_key(KEYCODE_ENTER)
        runner.run_adb.assert_called_with("inst1", "input keyevent 66")

        await adb.start_app("org.telegram.messenger")
        runner.run_adb.assert_called_with(
            "inst1",
            "monkey -p org.telegram.messenger -c android.intent.category.LAUNCHER 1",
        )

        await adb.set_reverse_proxy(8888)
        runner.run_adb.assert_any_call("inst1", "reverse tcp:8888 tcp:8888")
        runner.run_adb.assert_any_call("inst1", "settings put global http_proxy 127.0.0.1:8888")

    @pytest.mark.asyncio
    async def test_wait_for_boot(self) -> None:
        runner = MagicMock()
        runner.run_adb = AsyncMock(side_effect=["0", "1"])
        adb = ADBDriver("inst1", runner=runner)

        booted = await adb.wait_for_boot(timeout_seconds=2.0, poll_interval=0.05)
        assert booted is True


class TestDeviceProfile:
    """Hardware fingerprint generator tests."""

    def test_generates_deterministic_profile(self) -> None:
        p1 = generate_device_profile("acc-1")
        p2 = generate_device_profile("acc-1")
        p3 = generate_device_profile("acc-2")

        assert p1 == p2
        assert p1["imei"].startswith("86")
        assert len(p1["imei"]) == 15
        assert len(p1["android_id"]) == 16
        assert len(p1["mac"].split(":")) == 6
        assert p1["imei"] != p3["imei"]
        assert p1["android_id"] != p3["android_id"]
