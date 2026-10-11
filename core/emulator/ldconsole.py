"""LDPlayer 9 command-line interface wrapper (ldconsole.exe)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from core.emulator.config import (
    EmulatorLaunchError,
    EmulatorNotFoundError,
    get_ldconsole_path,
)

if TYPE_CHECKING:
    from pathlib import Path


class LDConsoleRunner:
    """Async runner for LDPlayer's ldconsole.exe CLI commands."""

    def __init__(
        self,
        executable_path: Path | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._executable_path = executable_path
        self._timeout_seconds = timeout_seconds

    @property
    def executable(self) -> Path:
        return self._executable_path or get_ldconsole_path()

    async def _exec(self, *args: str) -> str:
        exe = self.executable
        if not exe.is_file():
            msg = f"ldconsole.exe not found at {exe}"
            raise EmulatorNotFoundError(msg)

        proc = await asyncio.create_subprocess_exec(  # nosec B603
            str(exe),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self._timeout_seconds,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            cmd_name = args[0] if args else "command"
            msg = f"ldconsole {cmd_name} timed out after {self._timeout_seconds}s"
            raise EmulatorLaunchError(msg) from None

        out = stdout.decode("utf-8", errors="replace").strip()
        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace").strip()
            msg = f"ldconsole command failed (exit {proc.returncode}): {err or out}"
            raise EmulatorLaunchError(msg)
        return out

    async def is_running(self, name: str) -> bool:
        """Check whether the instance is currently running."""
        try:
            out = await self._exec("isrunning", "--name", name)
            return out.strip().lower() == "running"
        except (EmulatorLaunchError, OSError):
            return False

    async def create_instance(self, name: str) -> int:
        """Create a fresh instance and return its index."""
        out = await self._exec("add", "--name", name)
        # ldconsole add output may include the instance index or ID
        for part in out.split():
            if part.isdigit():
                return int(part)
        return 0

    async def set_device_profile(
        self,
        name: str,
        model: str,
        imei: str,
        android_id: str,
        mac: str,
    ) -> None:
        """Configure hardware identity of an instance."""
        await self._exec(
            "modify",
            "--name",
            name,
            "--model",
            model,
            "--imei",
            imei,
            "--androidid",
            android_id,
            "--mac",
            mac,
        )

    async def set_resolution(
        self,
        name: str,
        width: int = 412,
        height: int = 915,
        dpi: int = 320,
    ) -> None:
        """Set screen resolution to compact vertical smartphone layout."""
        await self._exec(
            "modify",
            "--name",
            name,
            "--resolution",
            f"{width},{height},{dpi}",
        )

    async def set_resource_limits(
        self,
        name: str,
        cpus: int = 1,
        ram_mb: int = 1536,
    ) -> None:
        """Set minimal resource limits for low footprint."""
        await self._exec(
            "modify",
            "--name",
            name,
            "--cpu",
            str(cpus),
            "--memory",
            str(ram_mb),
        )

    async def launch_instance(self, name: str) -> None:
        """Start the emulator instance window."""
        await self._exec("launch", "--name", name)

    async def stop_instance(self, name: str) -> None:
        """Gracefully stop the emulator instance."""
        await self._exec("quit", "--name", name)

    async def install_app(self, name: str, apk_path: str) -> None:
        """Install an APK file into the instance."""
        await self._exec("installapp", "--name", name, "--filename", str(apk_path))

    async def run_adb(self, name: str, command: str) -> str:
        """Execute an ADB command against the specified instance via ldconsole."""
        return await self._exec("adb", "--name", name, "--command", command)


_default_runner = LDConsoleRunner()


def get_default_runner() -> LDConsoleRunner:
    """Return the default module-level LDConsoleRunner instance."""
    return _default_runner


async def is_running(name: str) -> bool:
    """Check whether the named LDPlayer instance is currently running."""
    return await _default_runner.is_running(name)


async def create_instance(name: str) -> int:
    """Create a new LDPlayer instance by name and return its index."""
    return await _default_runner.create_instance(name)


async def set_device_profile(
    name: str,
    model: str,
    imei: str,
    android_id: str,
    mac: str,
) -> None:
    """Configure device hardware properties (model, IMEI, android ID, MAC) for an instance."""
    await _default_runner.set_device_profile(name, model, imei, android_id, mac)


async def set_resolution(
    name: str,
    width: int = 412,
    height: int = 915,
    dpi: int = 320,
) -> None:
    """Configure screen resolution and DPI for an LDPlayer instance."""
    await _default_runner.set_resolution(name, width, height, dpi)


async def set_resource_limits(
    name: str,
    cpus: int = 1,
    ram_mb: int = 1536,
) -> None:
    """Configure CPU and RAM resource limits for an LDPlayer instance."""
    await _default_runner.set_resource_limits(name, cpus, ram_mb)


async def launch_instance(name: str) -> None:
    """Start and display an LDPlayer emulator instance."""
    await _default_runner.launch_instance(name)


async def stop_instance(name: str) -> None:
    """Quit and stop an LDPlayer emulator instance."""
    await _default_runner.stop_instance(name)


async def install_app(name: str, apk_path: str) -> None:
    """Install an APK file into an LDPlayer emulator instance."""
    await _default_runner.install_app(name, apk_path)


async def run_adb(name: str, command: str) -> str:
    """Execute an ADB command against an LDPlayer emulator instance via ldconsole."""
    return await _default_runner.run_adb(name, command)
