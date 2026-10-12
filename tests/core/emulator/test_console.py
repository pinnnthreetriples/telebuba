"""ldconsole and adb: argv lists, ``shell`` on every device command, serials from list2."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.emulator import adb
from core.emulator._errors import EmulatorError
from core.emulator._profile import ContainerSpec
from core.emulator.ldconsole import (
    adb_serial,
    create_instance,
    find_instance,
    parse_list2,
    remove_container,
)
from core.emulator.ui_dump import UiNode

if TYPE_CHECKING:
    from tests.core.emulator.conftest import Console

_LIST2 = (
    "0,LDPlayer,0,0,0,-1,-1,960,540,240\n"
    "1,telebuba-acc-1,1312,1314,1,4242,4243,720,1280,320\n"
    "2,telebuba-acc-2,0,0,0,-1,-1,720,1280,320\n"
)


@pytest.mark.parametrize(
    ("index", "serial"), [(0, "emulator-5554"), (1, "emulator-5556"), (5, "emulator-5564")]
)
def test_the_adb_serial_follows_the_instance_index(index: int, serial: str) -> None:
    assert adb_serial(index) == serial


def test_list2_is_parsed_into_indexed_named_instances() -> None:
    instances = parse_list2(_LIST2 + "garbage line\n")

    assert [(i.index, i.name, i.running) for i in instances] == [
        (0, "LDPlayer", False),
        (1, "telebuba-acc-1", True),
        (2, "telebuba-acc-2", False),
    ]


@pytest.mark.asyncio
async def test_a_container_is_found_by_name_and_its_serial_by_its_own_index(
    console: Console,
) -> None:
    console.answer = lambda argv: _LIST2 if argv[1] == "list2" else ""

    instance = await find_instance("telebuba-acc-2")

    assert instance is not None
    assert adb_serial(instance.index) == "emulator-5558", "never the first emulator's serial"


@pytest.mark.asyncio
async def test_a_new_container_presents_the_mobile_fingerprints_phone(console: Console) -> None:
    created = {"done": False}

    def answer(argv: list[str]) -> str:
        if argv[1] == "add":
            created["done"] = True
        if argv[1] == "list2":
            return _LIST2 + ("3,telebuba-new,0,0,0,-1,-1,720,1280,320\n" if created["done"] else "")
        return ""

    console.answer = answer
    spec = ContainerSpec("samsung", "SM-S918B", 34, "490154203237518")

    instance = await create_instance("telebuba-new", spec)

    assert instance.index == 3
    modify = next(call for call in console.calls if call[1] == "modify")
    assert modify[0] == str(settings.emulator.ldconsole_path)
    assert modify[2:4] == ["--index", "3"]
    options = dict(zip(modify[4::2], modify[5::2], strict=True))
    assert options == {
        "--resolution": settings.emulator.resolution,
        "--cpu": str(settings.emulator.cpu_cores),
        "--memory": str(settings.emulator.memory_mb),
        "--manufacturer": "samsung",
        "--model": "SM-S918B",
        "--imei": "490154203237518",
    }


@pytest.mark.asyncio
async def test_removing_a_running_container_quits_it_first(console: Console, tmp_path) -> None:
    (tmp_path / "ldconsole.exe").write_bytes(b"")
    console.answer = lambda argv: _LIST2 if argv[1] == "list2" else ""

    assert await remove_container("telebuba-acc-1") is True

    assert [call[1:] for call in console.calls if call[1] != "list2"] == [
        ["quit", "--index", "1"],
        ["remove", "--index", "1"],
    ]


@pytest.mark.asyncio
async def test_a_host_without_ldplayer_has_no_container_to_remove(console: Console) -> None:
    assert await remove_container("telebuba-acc-1") is False
    assert console.calls == []


@pytest.mark.asyncio
async def test_every_device_command_goes_through_adb_shell(console: Console) -> None:
    node = UiNode("", "", "android.widget.EditText", password=False, bounds=(0, 100, 200, 140))

    await adb.tap("emulator-5556", node)
    await adb.type_text("emulator-5556", "a b$c")
    await adb.press("emulator-5556", adb.KEYCODE_ENTER)
    await adb.getprop("emulator-5556", "sys.boot_completed")

    for call in console.calls:
        assert call[:4] == [str(settings.emulator.adb_path), "-s", "emulator-5556", "shell"]
    assert console.shell_calls() == [
        ["input", "tap", "100", "120"],
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
