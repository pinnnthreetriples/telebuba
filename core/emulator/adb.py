"""ADB interaction and Android UI automation driver."""

from __future__ import annotations

import asyncio
import contextlib
import re
import xml.etree.ElementTree as ET  # nosec B405
from typing import TYPE_CHECKING

from core.emulator.config import EmulatorLaunchError
from core.emulator.ldconsole import get_default_runner

if TYPE_CHECKING:
    from core.emulator.ldconsole import LDConsoleRunner

# Keycodes for common Android actions
KEYCODE_ENTER = 66
KEYCODE_BACK = 4
KEYCODE_TAB = 61
KEYCODE_DEL = 67


def parse_element_center(xml_content: str, text: str) -> tuple[int, int] | None:
    """Find the center (x, y) coordinates of an element in UI hierarchy XML."""
    if not xml_content:
        return None

    try:
        root = ET.fromstring(xml_content)  # nosec B314 # noqa: S314
        for elem in root.iter():
            elem_text = elem.attrib.get("text", "")
            elem_desc = elem.attrib.get("content-desc", "")
            elem_id = elem.attrib.get("resource-id", "")
            if (
                text.lower() in elem_text.lower()
                or text.lower() in elem_desc.lower()
                or text.lower() in elem_id.lower()
            ):
                bounds = elem.attrib.get("bounds", "")
                m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds)
                if m:
                    x1, y1, x2, y2 = map(int, m.groups())
                    return ((x1 + x2) // 2, (y1 + y2) // 2)
    except (ET.ParseError, ValueError, AttributeError, TypeError):
        # Fallback to regex when XML is malformed
        pattern = re.compile(
            r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"[^>]*?(?:text|content-desc)="'
            + re.escape(text),
            re.IGNORECASE,
        )
        m = pattern.search(xml_content)
        if m:
            x1, y1, x2, y2 = map(int, m.groups())
            return ((x1 + x2) // 2, (y1 + y2) // 2)

    return None


class ADBDriver:
    """Driver for controlling an Android emulator instance via ADB."""

    def __init__(
        self,
        instance_name: str,
        runner: LDConsoleRunner | None = None,
    ) -> None:
        self.instance_name = instance_name
        self._runner = runner or get_default_runner()

    async def shell(self, command: str) -> str:
        """Execute a shell command inside the emulator instance."""
        return await self._runner.run_adb(self.instance_name, command)

    async def wait_for_boot(
        self,
        timeout_seconds: float = 60.0,
        poll_interval: float = 2.0,
    ) -> bool:
        """Poll until Android system boot has completed."""
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            with contextlib.suppress(EmulatorLaunchError, OSError):
                res = await self.shell("getprop sys.boot_completed")
                if res.strip() == "1":
                    return True
            await asyncio.sleep(poll_interval)
        return False

    async def start_app(
        self,
        package: str = "org.telegram.messenger",
        activity: str | None = None,
    ) -> None:
        """Launch an app by package name."""
        if activity:
            await self.shell(f"am start -n {package}/{activity}")
        else:
            await self.shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")

    async def stop_app(self, package: str = "org.telegram.messenger") -> None:
        """Force-stop an app package."""
        await self.shell(f"am force-stop {package}")

    async def tap(self, x: int, y: int) -> None:
        """Send a touch tap event at (x, y) coordinates."""
        await self.shell(f"input tap {x} {y}")

    async def tap_element_or_coords(self, x: int, y: int) -> None:
        """Alias for tap."""
        await self.tap(x, y)

    async def input_text(self, text: str) -> None:
        """Type text cleanly via ADB, escaping shell-sensitive characters."""
        # Spaces in `input text` must be escaped as %s; special chars escaped
        escaped = (
            text.replace(" ", "%s")
            .replace("&", "\\&")
            .replace("<", "\\<")
            .replace(">", "\\>")
            .replace("'", "\\'")
            .replace('"', '\\"')
        )
        await self.shell(f"input text {escaped}")

    async def press_key(self, keycode: int) -> None:
        """Send an Android key event by keycode."""
        await self.shell(f"input keyevent {keycode}")

    async def dump_ui_hierarchy(self) -> str:
        """Dump the current UI hierarchy XML using uiautomator."""
        await self.shell("uiautomator dump /data/local/tmp/uidump.xml")
        return await self.shell("cat /data/local/tmp/uidump.xml")

    async def wait_for_text(
        self,
        expected: str,
        timeout_seconds: float = 15.0,
        poll_interval: float = 1.0,
    ) -> bool:
        """Poll the UI hierarchy until expected text appears or timeout occurs."""
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            with contextlib.suppress(EmulatorLaunchError, OSError):
                dump = await self.dump_ui_hierarchy()
                if expected.lower() in dump.lower():
                    return True
            await asyncio.sleep(poll_interval)
        return False

    async def find_element_coords(self, text: str) -> tuple[int, int] | None:
        """Locate element on screen matching text and return its center coordinates."""
        with contextlib.suppress(EmulatorLaunchError, OSError):
            dump = await self.dump_ui_hierarchy()
            return parse_element_center(dump, text)
        return None

    async def get_current_activity(self) -> str:
        """Return the currently focused activity."""
        out = await self.shell("dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'")
        return out.strip()

    async def set_reverse_proxy(self, relay_port: int) -> None:
        """Set up ADB reverse port forwarding and global HTTP proxy in Android."""
        await self.shell(f"reverse tcp:{relay_port} tcp:{relay_port}")
        await self.shell(f"settings put global http_proxy 127.0.0.1:{relay_port}")

    async def clear_proxy(self) -> None:
        """Clear the global HTTP proxy settings in Android."""
        await self.shell("settings put global http_proxy :0")
        await self.shell("settings delete global http_proxy")
        await self.shell("settings delete global global_http_proxy_host")
        await self.shell("settings delete global global_http_proxy_port")
