"""adb: argv lists, ``shell`` on every device command, boot and SDK checks, the APK."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.emulator import adb
from core.emulator._errors import EmulatorError

if TYPE_CHECKING:
    from tests.core.emulator.conftest import Console


@pytest.mark.asyncio
async def test_every_device_command_goes_through_adb_shell(console: Console) -> None:

    await adb.type_text("emulator-5556", "a b$c")
    await adb.press("emulator-5556", adb.KEYCODE_ENTER)
    await adb.getprop("emulator-5556", "sys.boot_completed")

    for call in console.calls:
        assert call[:4] == [str(settings.emulator.adb_path), "-s", "emulator-5556", "shell"]
    assert console.shell_calls() == [
        ["input", "text", "a%sb\\$c"],
        ["input", "keyevent", "66"],
        ["getprop", "sys.boot_completed"],
    ]


@pytest.mark.asyncio
async def test_boot_is_awaited_until_android_says_so(console: Console) -> None:
    answers = iter(["", "0", "1"])
    console.answer = lambda _argv: next(answers)

    await adb.wait_for_boot("emulator-5554")

    assert len(console.calls) == 3


@pytest.mark.asyncio
async def test_a_container_that_never_boots_times_out(console: Console) -> None:
    console.answer = lambda _argv: "0"

    with pytest.raises(EmulatorError) as failed:
        await adb.wait_for_boot("emulator-5554")

    assert failed.value.code == "emulator_boot_timeout"


@pytest.mark.asyncio
async def test_an_sdk_other_than_the_fingerprints_is_refused(console: Console) -> None:
    console.answer = lambda _argv: "33\n"

    await adb.assert_sdk("emulator-5554", 33)
    with pytest.raises(EmulatorError) as refused:
        await adb.assert_sdk("emulator-5554", 34)

    assert refused.value.code == "emulator_sdk_mismatch"


@pytest.mark.asyncio
async def test_the_app_starts_with_no_system_keys(console: Console) -> None:
    # redroid has no physical keys: monkey's default 2% of SYS_KEYS makes it abort
    # (exit 251) before it launches anything.

    await adb.start_app("emulator-5554", "org.telegram.messenger.web")

    assert console.shell_calls() == [
        [
            *("monkey", "--pct-syskeys", "0", "-p", "org.telegram.messenger.web"),
            *("-c", "android.intent.category.LAUNCHER", "1"),
        ],
    ]


@pytest.mark.asyncio
async def test_the_package_list_is_read_whole(console: Console) -> None:
    console.answer = lambda _argv: "package:org.telegram.messenger.web\npackage:com.android.shell\n"

    assert await adb.installed_packages("emulator-5554") == {
        "org.telegram.messenger.web",
        "com.android.shell",
    }
    assert console.shell_calls()[-1] == ["pm", "list", "packages"]


@pytest.mark.asyncio
async def test_a_missing_apk_is_refused_before_adb_runs(
    console: Console,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.emulator, "telegram_apk_path", tmp_path / "missing.apk")

    with pytest.raises(EmulatorError) as refused:
        await adb.install_apk("emulator-5554")

    assert refused.value.code == "emulator_apk_missing"
    assert console.calls == []


@pytest.mark.asyncio
async def test_a_failed_install_has_its_own_code(
    console: Console,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apk = tmp_path / "telegram.apk"
    apk.write_bytes(b"apk")
    monkeypatch.setattr(settings.emulator, "telegram_apk_path", apk)

    def fail(_argv: list[str]) -> str:
        code = "emulator_command_failed"
        raise EmulatorError(code)

    console.answer = fail

    with pytest.raises(EmulatorError) as refused:
        await adb.install_apk("emulator-5554")

    assert refused.value.code == "emulator_install_failed"
    assert console.calls[0][3:] == ["install", "-r", "-i", "com.android.vending", str(apk)]


@pytest.mark.asyncio
async def test_install_apk_supports_custom_or_no_installer_package(
    console: Console,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apk = tmp_path / "telegram.apk"
    apk.write_bytes(b"apk")
    monkeypatch.setattr(settings.emulator, "telegram_apk_path", apk)

    console.answer = lambda _argv: "Success"
    await adb.install_apk("emulator-5554", installer_package=None)
    assert console.calls[-1][3:] == ["install", "-r", str(apk)]

    await adb.install_apk("emulator-5554", installer_package="org.custom.store")
    assert console.calls[-1][3:] == ["install", "-r", "-i", "org.custom.store", str(apk)]


@pytest.mark.asyncio
async def test_install_apk_accepts_explicit_apk_path(
    console: Console,
    tmp_path,
) -> None:
    custom_apk = tmp_path / "custom.apk"
    custom_apk.write_bytes(b"custom-apk-content")
    await adb.install_apk("emulator-5554", apk_path=custom_apk)

    assert console.calls[0][3:] == ["install", "-r", "-i", "com.android.vending", str(custom_apk)]


@pytest.mark.asyncio
async def test_step_battery_discharge_decrements_level(
    console: Console,
) -> None:
    console.answer = lambda argv: "  level: 80\n  status: 3" if "dumpsys" in argv else ""
    await adb.step_battery_discharge("emulator-5554", decrement=3)

    set_call = console.shell_calls()[-1]
    assert set_call == ["dumpsys", "battery", "set", "level", "77"]


@pytest.mark.asyncio
async def test_a_missing_adb_is_not_waited_out_as_a_slow_boot(console: Console) -> None:
    def missing(_argv: list[str]) -> str:
        code = "emulator_not_found"
        raise EmulatorError(code)

    console.answer = missing

    with pytest.raises(EmulatorError) as failed:
        await adb.wait_for_boot("emulator-5554")

    assert failed.value.code == "emulator_not_found"
    assert len(console.calls) == 1


@pytest.mark.asyncio
async def test_a_dump_is_always_fresh_or_nothing(console: Console) -> None:
    console.answer = lambda _argv: "ERROR: could not get idle state."

    assert await adb.dump_ui("emulator-5554") == ""
    tries = console.shell_calls()
    assert len(tries) == 3, "one script a try, three tries"
    first_commands = [script[script.index(";") + 1 :] for script in tries]
    xmls = [script[2] for script in first_commands]
    assert all(script[:2] == ["rm", "-f"] for script in first_commands), (
        "an old file is never read back"
    )
    assert all(xml.startswith("/sdcard/telebuba_ui_") for xml in xmls)
    assert len(set(xmls)) == 3, "each try dumps into its own file"


@pytest.mark.asyncio
async def test_a_failed_dump_is_taken_again(console: Console) -> None:
    answers = iter(
        [
            "ERROR: could not get idle state.\n",
            "UI hierchary dumped to: /sdcard/telebuba_ui.xml\n<hierarchy/>",
        ]
    )
    console.answer = lambda _argv: next(answers)

    assert await adb.dump_ui("emulator-5554") == "<hierarchy/>"
    assert len(console.shell_calls()) == 2


@pytest.mark.asyncio
async def test_a_taken_dump_is_read_back(console: Console) -> None:
    console.answer = lambda _argv: "UI hierchary dumped to: /sdcard/telebuba_ui.xml\n<hierarchy/>"

    assert await adb.dump_ui("emulator-5554") == "<hierarchy/>"
