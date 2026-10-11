"""Async wrapper around LDPlayer 9's ``ldconsole.exe`` CLI."""

# ruff: noqa: ASYNC109

from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING

from core.emulator.config import emulator_settings

if TYPE_CHECKING:
    from core.emulator.config import EmulatorSettings

__all__ = [
    "EmulatorCommandError",
    "EmulatorError",
    "EmulatorNotFoundError",
    "create_instance",
    "deterministic_hardware_ids",
    "is_running",
    "launch_instance",
    "set_device_profile",
    "set_resolution",
    "set_resource_limits",
    "stop_instance",
]


class EmulatorError(RuntimeError):
    """Base error for LDPlayer / ADB subsystem failures."""


class EmulatorNotFoundError(EmulatorError):
    """The configured ``ldconsole.exe`` or ``adb.exe`` binary is missing on disk."""


class EmulatorCommandError(EmulatorError):
    """A subprocess command exited with a non-zero status or timed out."""


def deterministic_hardware_ids(account_id: str) -> tuple[str, str, str]:
    """Derive stable (imei, android_id, mac) identifiers for ``account_id``."""
    digest = hashlib.sha256(f"telebuba-hw:{account_id}".encode()).hexdigest()
    # 15-digit numeric IMEI starting with standard TAC prefix '35'
    numeric = str(int(digest[:16], 16)).zfill(13)[:13]
    imei = f"35{numeric}"
    # 16-char lowercase hex Android ID
    android_id = digest[16:32]
    # Locally administered unicast MAC address (02:xx:xx:xx:xx:xx)
    octets = ["02", *[digest[i : i + 2] for i in range(32, 42, 2)]]
    mac = ":".join(octets)
    return imei, android_id, mac


async def _run_subprocess(
    argv: list[str],
    *,
    timeout: float = 30.0,
) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        msg = f"emulator command timed out after {timeout}s"
        raise EmulatorCommandError(msg) from exc
    return (
        proc.returncode or 0,
        stdout_b.decode("utf-8", errors="replace").strip(),
        stderr_b.decode("utf-8", errors="replace").strip(),
    )


async def _exec_ldconsole(
    args: list[str],
    *,
    cfg: EmulatorSettings | None = None,
    timeout: float = 30.0,
    check: bool = True,
) -> tuple[int, str, str]:
    active_cfg = cfg or emulator_settings
    binary = active_cfg.ldconsole_path
    if not binary.is_file():
        msg = f"ldconsole binary not found: {binary}"
        raise EmulatorNotFoundError(msg)
    code, stdout, stderr = await _run_subprocess([str(binary), *args], timeout=timeout)
    if check and code != 0:
        msg = f"ldconsole {args[0] if args else ''} failed with exit code {code}: {stderr}"
        raise EmulatorCommandError(msg)
    return code, stdout, stderr


async def create_instance(
    name: str,
    *,
    cfg: EmulatorSettings | None = None,
) -> None:
    """Create an LDPlayer instance named ``name`` (idempotent if already present)."""
    await _exec_ldconsole(["add", "--name", name], cfg=cfg, check=False)


async def set_device_profile(  # noqa: PLR0913
    name: str,
    *,
    model: str,
    imei: str,
    android_id: str,
    mac: str,
    cfg: EmulatorSettings | None = None,
) -> None:
    """Configure the instance's hardware fingerprint before boot."""
    manufacturer = model.split(maxsplit=1)[0] if model.strip() else "Samsung"
    await _exec_ldconsole(
        [
            "modify",
            "--name",
            name,
            "--manufacturer",
            manufacturer,
            "--model",
            model,
            "--imei",
            imei,
            "--androidid",
            android_id,
            "--mac",
            mac,
        ],
        cfg=cfg,
    )


async def set_resolution(
    name: str,
    *,
    width: int = 412,
    height: int = 915,
    dpi: int = 320,
    cfg: EmulatorSettings | None = None,
) -> None:
    """Configure compact vertical smartphone screen dimensions (412x915 DPI 320)."""
    await _exec_ldconsole(
        [
            "modify",
            "--name",
            name,
            "--resolution",
            f"{width},{height},{dpi}",
        ],
        cfg=cfg,
    )


async def set_resource_limits(
    name: str,
    *,
    cpus: int = 1,
    ram_mb: int = 1536,
    cfg: EmulatorSettings | None = None,
) -> None:
    """Set CPU and RAM limits for the instance."""
    await _exec_ldconsole(
        [
            "modify",
            "--name",
            name,
            "--cpu",
            str(cpus),
            "--memory",
            str(ram_mb),
        ],
        cfg=cfg,
    )


async def launch_instance(
    name: str,
    *,
    cfg: EmulatorSettings | None = None,
) -> None:
    """Start the LDPlayer instance window."""
    await _exec_ldconsole(["launch", "--name", name], cfg=cfg)


async def stop_instance(
    name: str,
    *,
    cfg: EmulatorSettings | None = None,
) -> None:
    """Gracefully stop the LDPlayer instance."""
    await _exec_ldconsole(["quit", "--name", name], cfg=cfg, check=False)


async def is_running(
    name: str,
    *,
    cfg: EmulatorSettings | None = None,
) -> bool:
    """Return True when the named LDPlayer instance is currently running."""
    code, stdout, _ = await _exec_ldconsole(["isrunning", "--name", name], cfg=cfg, check=False)
    return code == 0 and stdout.strip().lower() == "running"
