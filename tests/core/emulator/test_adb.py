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
async def test_the_package_list_is_matched_exactly(console: Console) -> None:
    console.answer = lambda _argv: "package:org.telegram.messenger.web\n"
    assert await adb.package_installed("emulator-5554", "org.telegram.messenger") is False

    console.answer = lambda _argv: "package:org.telegram.messenger\n"
    assert await adb.package_installed("emulator-5554", "org.telegram.messenger") is True
    assert console.shell_calls()[-1] == ["pm", "list", "packages", "org.telegram.messenger"]


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
    assert console.calls[0][3:] == ["install", "-r", str(apk)]


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
    assert (
        console.shell_calls()
        == [
            [
                "rm",
                "-f",
                "/sdcard/telebuba_ui.xml",
                ";",
                "uiautomator",
                "dump",
                "/sdcard/telebuba_ui.xml",
                ";",
                "cat",
                "/sdcard/telebuba_ui.xml",
                "2>/dev/null",
                ";",
                "true",
            ]
        ]
        * 3
    ), "one script a try, three tries, and an old file is never read back"


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
