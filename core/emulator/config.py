"""Configuration, paths, and exceptions for native mobile emulator subsystem."""

from __future__ import annotations

import hashlib
from pathlib import Path

from core.config import settings


class EmulatorError(Exception):
    """Base exception for emulator operations."""


class EmulatorNotFoundError(ValueError, EmulatorError):
    """LDPlayer or ADB binary not located on host."""


class NoProxyForMobileError(ValueError, EmulatorError):
    """Account has no assigned proxy."""


class LoginCodeTimeoutError(ValueError, EmulatorError):
    """Telethon did not receive code from 777000 within timeout."""


class EmulatorLaunchError(ValueError, EmulatorError):
    """Failed to launch or configure emulator instance."""


class EmulatorAdbError(ValueError, EmulatorError):
    """ADB command execution failed."""


def get_ldplayer_dir() -> Path:
    """Return the configured LDPlayer directory."""
    return settings.emulator.ldplayer_dir


def get_ldconsole_path() -> Path:
    """Return path to ldconsole.exe."""
    return get_ldplayer_dir() / "ldconsole.exe"


def get_adb_path() -> Path:
    """Return path to bundled adb.exe or fallback."""
    ld_adb = get_ldplayer_dir() / "adb.exe"
    if ld_adb.is_file():
        return ld_adb
    return Path("adb")


def get_apk_path() -> Path:
    """Return path to Telegram Android APK."""
    return settings.emulator.apk_path


def is_emulator_available() -> bool:
    """Return True if LDPlayer executable is present on host."""
    return get_ldconsole_path().is_file()


def generate_device_profile(account_id: str) -> dict[str, str]:
    """Generate deterministic hardware fingerprints for an account.

    Derives IMEI, Android ID, and MAC address deterministically from account_id
    so repeated launches present the identical physical device footprint.
    """
    digest = hashlib.sha256(f"tb_hw_{account_id}".encode()).hexdigest()

    # 15-digit IMEI (standard Type Allocation Code prefix 86...)
    raw_digits = "".join(str(int(c, 16) % 10) for c in digest[:20])
    imei = "86" + raw_digits[:13]

    # 16-hex Android ID
    android_id = digest[:16].lower()

    # 6-byte MAC address formatted as XX:XX:XX:XX:XX:XX
    mac_parts = [digest[i : i + 2].upper() for i in range(16, 28, 2)]
    mac = ":".join(mac_parts)

    return {
        "model": "Samsung Galaxy S23",
        "imei": imei,
        "android_id": android_id,
        "mac": mac,
    }
