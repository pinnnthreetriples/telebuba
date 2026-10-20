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
from core.emulator._process import adb_command
from core.emulator._text import escape_input_text, shell_quote

if TYPE_CHECKING:
    from core.emulator.ui_dump import UiNode
    from schemas.emulator import EmulatorRefusalCode

_EMULATOR_APK_MISSING: EmulatorRefusalCode = "emulator_apk_missing"
_EMULATOR_BOOT_TIMEOUT: EmulatorRefusalCode = "emulator_boot_timeout"
_EMULATOR_INSTALL_FAILED: EmulatorRefusalCode = "emulator_install_failed"
_EMULATOR_SDK_MISMATCH: EmulatorRefusalCode = "emulator_sdk_mismatch"

KEYCODE_HOME = 3
KEYCODE_BACK = 4
KEYCODE_ENTER = 66
KEYCODE_DEL = 67
KEYCODE_MOVE_END = 123
_DUMP_PATH = "/sdcard/telebuba_ui.xml"
_DUMP_LOG = "/sdcard/telebuba_ui.log"
_DUMP_ATTEMPTS = 3
# A slow drag's Bezier curve goes out as this many points: each ``input motionevent``
# starts its own process on the device, and the curve keeps its shape at a dozen.
_SWIPE_POINTS = (8, 12)
_BOOTED = "1"


def _dump_script(xml: str, log: str) -> tuple[str, ...]:
    """The device-side script of one dump into ``xml``, its own output kept in ``log``.

    uiautomator writes the file, prints "dumped to" and then spends most of a second
    shutting its VM down; the script kills it at that print (or stops when it has
    exited without one) and serves its output followed by the file.
    """
    return (
        *("rm", "-f", xml, log, ";"),
        *("uiautomator", "dump", xml, f">{log}", "2>&1", "&", "p=$!", ";"),
        *("while", "kill", "-0", "$p", "2>/dev/null", "&&", "!", "grep", "-q", "dumped", log),
        *(";", "do", "sleep", "0.05", ";", "done", ";"),
        *("kill", "$p", "2>/dev/null", ";"),
        *("cat", log, xml, "2>/dev/null", ";"),
        *("rm", "-f", log, ";", "true"),
    )


_DUMP_SCRIPT = _dump_script(_DUMP_PATH, _DUMP_LOG)


async def shell(serial: str, *args: str, seconds: float | None = None) -> str:
    return await adb_command(
        "-s", serial, "shell", *args, seconds=seconds, step=f"adb shell {args[0]}"
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
        await adb_command("connect", serial, step="adb connect")


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


async def installed_packages(serial: str) -> set[str]:
    listed = await shell(serial, "pm", "list", "packages")
    return {line.strip().removeprefix("package:") for line in listed.splitlines() if line.strip()}


async def install_apk(serial: str) -> None:
    """Install the configured Telegram APK; ``emulator_apk_missing`` when there is no file."""
    apk = settings.emulator.telegram_apk_path
    if not apk.is_file():
        raise EmulatorError(_EMULATOR_APK_MISSING)
    try:
        await adb_command(
            "-s",
            serial,
            "install",
            "-r",
            str(apk),
            seconds=settings.emulator.install_timeout_seconds,
            step="adb install",
        )
    except EmulatorError as exc:
        if exc.code == "emulator_command_failed":
            raise EmulatorError(_EMULATOR_INSTALL_FAILED) from exc
        raise


async def push(serial: str, *local: str, remote: str) -> None:
    """Copy host files to ``remote`` on the device (a directory when there are several)."""
    await adb_command(
        "-s",
        serial,
        "push",
        *local,
        remote,
        seconds=settings.emulator.install_timeout_seconds,
        step="adb push",
    )


async def start_app(serial: str, package: str) -> None:
    await shell(serial, "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1")


async def open_uri(serial: str, uri: str, package: str) -> None:
    """Open ``uri`` in ``package`` as a tapped link does (``tg://settings``, ``tg://resolve``)."""
    await shell(
        serial,
        *("am", "start", "-a", "android.intent.action.VIEW"),
        *("-d", shell_quote(uri), package),
    )


async def dump_ui(serial: str) -> str:
    """This moment's UI dump, or ``""`` when uiautomator could not take one in a few tries.

    One ``adb shell`` script (``_dump_script``) deletes the file, dumps and prints it:
    the file is deleted first because a failed dump ("could not get idle state",
    common on animated screens) still exits 0, and ``cat`` would then serve an older
    screen; the script always exits 0, and only the XML after uiautomator's "dumped
    to" line counts.
    A failed dump is taken again after a poll interval: one is no screen at all, and
    read as one it would send a BACK press or a tap on a guess.
    """
    for attempt in range(_DUMP_ATTEMPTS):
        if attempt:
            await asyncio.sleep(settings.emulator.poll_seconds)
        output = await shell(serial, *_DUMP_SCRIPT)
        marker = output.lower().find("dumped to")
        xml = output[marker:].partition("\n")[2].strip() if marker >= 0 else ""
        if xml:
            return xml
    return ""


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
    as_code: bool = False,
) -> None:
    """Type ``text`` with stochastic keystroke intervals and realistic typo-correction.

    ``as_code`` types a verification code instead: steady digits, no typos, and the
    mid-code glance of a six-digit code (``HumanInputEngine.generate_code_sequence``).
    """
    escape_input_text(text)
    eng = engine or _default_input_engine
    if as_code:
        events = eng.generate_code_sequence(text)
    elif typo_probability is not None:
        events = eng.generate_keystroke_sequence(text, typo_probability=typo_probability)
    else:
        events = eng.generate_keystroke_sequence(text)
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
    engine: HumanInputEngine | None = None,
) -> None:
    """Drag the finger along a sampled Bezier curve from ``start`` to ``end``.

    Every point goes out as one ``input motionevent`` (DOWN, MOVEs, UP) in a single
    ``adb shell`` script that the device's ``sh`` runs command by command.
    """
    eng = engine or _default_input_engine
    points = eng.generate_bezier_swipe(start, end, eng.rng.randint(*_SWIPE_POINTS)).points
    actions = ["DOWN"] + ["MOVE"] * (len(points) - 2) + ["UP"]
    script: list[str] = []
    for action, (x, y) in zip(actions, points, strict=True):
        if script:
            script.append(";")
        script += ["input", "motionevent", action, str(x), str(y)]
    await shell(serial, *script)


async def human_fling(
    serial: str,
    start: tuple[int, int],
    end: tuple[int, int],
    *,
    engine: HumanInputEngine | None = None,
) -> None:
    """Flick from ``start`` towards ``end`` fast enough for Android's scroll inertia."""
    event = (engine or _default_input_engine).sample_fling(start, end)
    (x1, y1), (x2, y2) = event.start, event.end
    await shell(
        serial, "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(event.duration_ms)
    )


async def double_tap(
    serial: str,
    node: UiNode,
    *,
    engine: HumanInputEngine | None = None,
) -> None:
    """Tap one sampled point of ``node`` twice in a single ``adb shell`` script."""
    event = (engine or _default_input_engine).sample_gaussian_tap(node.bounds)
    x, y = str(event.x), str(event.y)
    await shell(serial, "input", "tap", x, y, ";", "input", "tap", x, y)
