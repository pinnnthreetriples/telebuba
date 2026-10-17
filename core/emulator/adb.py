"""adb against one container: every device-side command goes through ``adb shell``.

Each call is an argv list (``adb -s <serial> shell <args...>``), so nothing on the
host side is parsed by a shell; the device's own ``sh`` gets text escaped by
``_text``. Nothing is ever tapped at a fixed coordinate: callers pass a node the UI
dump showed.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING

from core.config import settings
from core.emulator._errors import EmulatorError
from core.emulator._input import HumanInputEngine
from core.emulator._process import run_command
from core.emulator._text import escape_input_text

if TYPE_CHECKING:
    from core.emulator.ui_dump import UiNode
    from schemas.emulator import EmulatorRefusalCode

_EMULATOR_APK_MISSING: EmulatorRefusalCode = "emulator_apk_missing"
_EMULATOR_BOOT_TIMEOUT: EmulatorRefusalCode = "emulator_boot_timeout"
_EMULATOR_INSTALL_FAILED: EmulatorRefusalCode = "emulator_install_failed"
_EMULATOR_SDK_MISMATCH: EmulatorRefusalCode = "emulator_sdk_mismatch"

KEYCODE_ENTER = 66
KEYCODE_DEL = 67
KEYCODE_MOVE_END = 123
_DUMP_PATH = "/sdcard/telebuba_ui.xml"
_BOOTED = "1"


async def shell(serial: str, *args: str, seconds: float | None = None) -> str:
    return await run_command(
        [str(settings.emulator.adb_path), "-s", serial, "shell", *args],
        seconds=seconds or settings.emulator.command_timeout_seconds,
        step=f"adb shell {args[0]}",
    )


async def getprop(serial: str, key: str) -> str:
    return (await shell(serial, "getprop", key)).strip()


async def connect_device(serial: str) -> None:
    """Make adb see the container's network serial (``host:port``); others need nothing.

    A refused connection is not an error here: the next adb call against the serial
    fails on its own, and a booting container's adbd is simply not up yet.
    """
    if ":" not in serial:
        return
    with suppress(EmulatorError):
        await run_command(
            [str(settings.emulator.adb_path), "connect", serial],
            seconds=settings.emulator.command_timeout_seconds,
            step="adb connect",
        )


async def _boot_completed(serial: str) -> bool:
    """``False`` while booting — including while adb does not see the device at all yet."""
    await connect_device(serial)
    try:
        return await getprop(serial, "sys.boot_completed") == _BOOTED
    except EmulatorError as exc:
        if exc.code == "emulator_not_found":
            raise  # no adb binary at all: waiting cannot help
        # adb answers "device not found" until the container's adbd is up.
        return False


async def wait_for_boot(serial: str) -> None:
    """Return once Android reports boot completed; ``emulator_boot_timeout`` otherwise."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + settings.emulator.boot_timeout_seconds
    while loop.time() < deadline:
        if await _boot_completed(serial):
            return
        await asyncio.sleep(settings.emulator.poll_seconds)
    raise EmulatorError(_EMULATOR_BOOT_TIMEOUT)


async def assert_sdk(serial: str, sdk: int) -> None:
    """Refuse a container whose Android is not the SDK the mobile fingerprint claims."""
    if await getprop(serial, "ro.build.version.sdk") != str(sdk):
        raise EmulatorError(_EMULATOR_SDK_MISMATCH)


async def package_installed(serial: str, package: str) -> bool:
    listed = await shell(serial, "pm", "list", "packages", package)
    return f"package:{package}" in {line.strip() for line in listed.splitlines()}


async def install_apk(serial: str) -> None:
    """Install the configured Telegram APK; ``emulator_apk_missing`` when there is no file."""
    apk = settings.emulator.telegram_apk_path
    if not apk.is_file():
        raise EmulatorError(_EMULATOR_APK_MISSING)
    try:
        await run_command(
            [str(settings.emulator.adb_path), "-s", serial, "install", "-r", str(apk)],
            seconds=settings.emulator.install_timeout_seconds,
            step="adb install",
        )
    except EmulatorError as exc:
        if exc.code == "emulator_command_failed":
            raise EmulatorError(_EMULATOR_INSTALL_FAILED) from exc
        raise


async def push(serial: str, *local: str, remote: str) -> None:
    """Copy host files to ``remote`` on the device (a directory when there are several)."""
    await run_command(
        [str(settings.emulator.adb_path), "-s", serial, "push", *local, remote],
        seconds=settings.emulator.install_timeout_seconds,
        step="adb push",
    )


async def start_app(serial: str, package: str) -> None:
    await shell(serial, "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1")


async def dump_ui(serial: str) -> str:
    """This moment's UI dump, or ``""`` when uiautomator could not take one.

    The file is deleted first: a failed dump ("could not get idle state", common on
    animated screens) still exits 0, and ``cat`` would then serve an older screen.
    """
    await shell(serial, "rm", "-f", _DUMP_PATH)
    if "dumped to" not in (await shell(serial, "uiautomator", "dump", _DUMP_PATH)).lower():
        return ""
    return await shell(serial, "cat", _DUMP_PATH)


async def tap(serial: str, node: UiNode) -> None:
    x, y = node.center
    await shell(serial, "input", "tap", str(x), str(y))


async def press(serial: str, *keycodes: int) -> None:
    await shell(serial, "input", "keyevent", *(str(code) for code in keycodes))


async def type_text(serial: str, text: str) -> None:
    await shell(serial, "input", "text", escape_input_text(text))


_default_input_engine = HumanInputEngine()


async def human_tap(
    serial: str,
    node: UiNode,
    *,
    engine: HumanInputEngine | None = None,
) -> None:
    """Sample a Gaussian tap inside ``node.bounds`` and execute via swipe micro-slip."""
    eng = engine or _default_input_engine
    event = eng.sample_gaussian_tap(node.bounds)
    await shell(
        serial,
        "input",
        "swipe",
        str(event.x),
        str(event.y),
        str(event.release_x),
        str(event.release_y),
        str(event.hold_ms),
    )


async def human_type(
    serial: str,
    text: str,
    *,
    typo_probability: float | None = None,
    engine: HumanInputEngine | None = None,
) -> None:
    """Type ``text`` with stochastic keystroke intervals and realistic typo-correction."""
    escape_input_text(text)
    eng = engine or _default_input_engine
    events = (
        eng.generate_keystroke_sequence(text, typo_probability=typo_probability)
        if typo_probability is not None
        else eng.generate_keystroke_sequence(text)
    )
    for event in events:
        if event.delay_ms > 0:
            await asyncio.sleep(event.delay_ms / 1000.0)
        if event.key_event is not None:
            await press(serial, event.key_event)
        else:
            await type_text(serial, event.char)


async def human_swipe(
    serial: str,
    start: tuple[int, int],
    end: tuple[int, int],
    *,
    duration_ms: int | None = None,
    engine: HumanInputEngine | None = None,
) -> None:
    """Swipe from ``start`` to ``end`` over a sampled duration (or ``duration_ms``).

    ``input swipe`` moves in a straight line at constant speed: only the trajectory's
    end points and ``total_duration_ms`` reach the device, its arc and easing do not.
    """
    eng = engine or _default_input_engine
    trajectory = eng.generate_bezier_swipe(start, end)
    dur = duration_ms if duration_ms is not None else trajectory.total_duration_ms
    await shell(
        serial,
        "input",
        "swipe",
        str(trajectory.points[0][0]),
        str(trajectory.points[0][1]),
        str(trajectory.points[-1][0]),
        str(trajectory.points[-1][1]),
        str(dur),
    )
