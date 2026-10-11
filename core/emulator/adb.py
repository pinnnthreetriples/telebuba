"""ADB UI automation and proxy configuration driver for the Android emulator."""

# ruff: noqa: ASYNC109

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from core.emulator.config import emulator_settings
from core.emulator.ldconsole import (
    EmulatorCommandError,
    EmulatorNotFoundError,
    _run_subprocess,
)

if TYPE_CHECKING:
    from core.emulator.config import EmulatorSettings

__all__ = [
    "KEYCODE_BACK",
    "KEYCODE_ENTER",
    "clear_http_proxy",
    "configure_http_proxy",
    "dump_ui_xml",
    "input_text",
    "press_key",
    "start_app",
    "tap",
    "wait_for_boot",
    "wait_for_text",
]

KEYCODE_BACK = 4
KEYCODE_ENTER = 66
_TELEGRAM_PACKAGE = "org.telegram.messenger"
_TELEGRAM_ACTIVITY = "org.telegram.ui.LaunchActivity"


async def _exec_adb(
    serial: str,
    args: list[str],
    *,
    cfg: EmulatorSettings | None = None,
    timeout: float = 30.0,
    check: bool = True,
) -> tuple[int, str, str]:
    active_cfg = cfg or emulator_settings
    binary = active_cfg.adb_path
    if not binary.is_file():
        msg = f"adb binary not found: {binary}"
        raise EmulatorNotFoundError(msg)
    code, stdout, stderr = await _run_subprocess(
        [str(binary), "-s", serial, *args],
        timeout=timeout,
    )
    if check and code != 0:
        msg = f"adb command failed with exit code {code}: {stderr}"
        raise EmulatorCommandError(msg)
    return code, stdout, stderr


async def wait_for_boot(
    serial: str,
    *,
    timeout: float = 60.0,
    poll_interval: float = 1.0,
    cfg: EmulatorSettings | None = None,
) -> None:
    """Wait until Android reports ``sys.boot_completed == 1``."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code, stdout, _ = await _exec_adb(
            serial,
            ["shell", "getprop", "sys.boot_completed"],
            cfg=cfg,
            check=False,
        )
        if code == 0 and stdout.strip() == "1":
            return
        await asyncio.sleep(poll_interval)
    msg = f"Android boot did not complete within {timeout}s on {serial}"
    raise EmulatorCommandError(msg)


async def configure_http_proxy(
    serial: str,
    host: str,
    port: int,
    *,
    cfg: EmulatorSettings | None = None,
) -> None:
    """Route emulator HTTP/HTTPS traffic through ``host:port``."""
    await _exec_adb(
        serial,
        ["shell", "settings", "put", "global", "http_proxy", f"{host}:{port}"],
        cfg=cfg,
    )


async def clear_http_proxy(
    serial: str,
    *,
    cfg: EmulatorSettings | None = None,
) -> None:
    """Reset the global Android HTTP proxy setting."""
    await _exec_adb(
        serial,
        ["shell", "settings", "put", "global", "http_proxy", ":0"],
        cfg=cfg,
        check=False,
    )


async def start_app(
    serial: str,
    *,
    package: str = _TELEGRAM_PACKAGE,
    activity: str = _TELEGRAM_ACTIVITY,
    cfg: EmulatorSettings | None = None,
) -> None:
    """Launch the Telegram Android main activity."""
    await _exec_adb(
        serial,
        ["shell", "am", "start", "-n", f"{package}/{activity}"],
        cfg=cfg,
    )


async def tap(
    serial: str,
    x: int,
    y: int,
    *,
    cfg: EmulatorSettings | None = None,
) -> None:
    """Tap screen coordinates ``(x, y)`` via ADB."""
    await _exec_adb(
        serial,
        ["shell", "input", "tap", str(x), str(y)],
        cfg=cfg,
    )


async def input_text(
    serial: str,
    text: str,
    *,
    cfg: EmulatorSettings | None = None,
) -> None:
    """Type ``text`` into the focused Android input field."""
    escaped = text.replace(" ", "%s")
    await _exec_adb(
        serial,
        ["shell", "input", "text", escaped],
        cfg=cfg,
    )


async def press_key(
    serial: str,
    keycode: int,
    *,
    cfg: EmulatorSettings | None = None,
) -> None:
    """Send an Android keyevent (e.g. ``KEYCODE_ENTER = 66``)."""
    await _exec_adb(
        serial,
        ["shell", "input", "keyevent", str(keycode)],
        cfg=cfg,
    )


async def dump_ui_xml(
    serial: str,
    *,
    cfg: EmulatorSettings | None = None,
) -> str:
    """Capture the current UIAutomator hierarchy XML from stdout."""
    _, stdout, _ = await _exec_adb(
        serial,
        ["exec-out", "uiautomator", "dump", "/dev/tty"],
        cfg=cfg,
        check=False,
    )
    return stdout


async def wait_for_text(
    serial: str,
    expected: str,
    *,
    timeout: float = 15.0,
    poll_interval: float = 1.0,
    cfg: EmulatorSettings | None = None,
) -> bool:
    """Poll UIAutomator XML until ``expected`` substring appears or ``timeout`` expires."""
    deadline = time.monotonic() + timeout
    needle = expected.lower()
    while time.monotonic() < deadline:
        xml = await dump_ui_xml(serial, cfg=cfg)
        if needle in xml.lower():
            return True
        await asyncio.sleep(poll_interval)
    return False
