from __future__ import annotations

import secrets

from core.db import fetch_device_fingerprint, insert_device_fingerprint
from schemas.device_fingerprint import DeviceFingerprint, DevicePlatform

_WINDOWS_VERSIONS = (
    "Windows 10",
    "Windows 11",
    "Windows 10 Pro",
    "Windows 11 Pro",
    "Windows 10 Enterprise",
    "Windows 10 LTSC",
    "Windows 11 Enterprise",
)
_MACOS_VERSIONS = (
    "macOS 14.0",
    "macOS 14.1",
    "macOS 14.2",
    "macOS 13.6",
    "macOS 13.5",
    "macOS 14.3",
    "macOS 14.4",
    "macOS 14.5",
    "macOS 13.4",
    "macOS 12.7",
    "macOS 15.0",
    "macOS 15.1",
)
_LINUX_DISTROS = (
    "Ubuntu 22.04",
    "Ubuntu 23.10",
    "Ubuntu 24.04",
    "Fedora 39",
    "Fedora 40",
    "Arch Linux",
    "Debian 12",
    "Linux Mint 21.3",
    "Pop!_OS 22.04",
    "openSUSE 15.5",
    "Manjaro 23.1",
)
_DESKTOP_DEVICES = ("Desktop", "PC", "Laptop", "Workstation")
_MAC_DEVICES = (
    "MacBook Pro",
    "MacBook Air",
    "iMac",
    "Mac mini",
    "Mac Studio",
    "Mac Pro",
    'MacBook Pro 14"',
    'MacBook Pro 16"',
    "MacBook Air M2",
    "MacBook Air M3",
    'iMac 24"',
    "MacBook Pro M3",
)
_WINDOWS_APP_VERSIONS = (
    "4.14.9 x64",
    "4.15.0 x64",
    "4.15.2 x64",
    "4.16.0 x64",
    "4.16.2 x64",
    "4.16.6 x64",
    "4.16.8 x64",
    "5.0.1 x64",
    "5.0.2 x64",
    "5.1.0 x64",
    "5.1.1 x64",
    "5.2.0 x64",
    "5.2.1 x64",
    "5.2.3 x64",
    "5.3.0 x64",
    "5.3.1 x64",
    "5.3.2 x64",
    "5.4.0 x64",
)
_MAC_APP_VERSIONS = (
    "10.3.1",
    "10.3.2",
    "10.4.0",
    "10.4.1",
    "10.4.2",
    "10.5.0",
    "10.5.1",
    "10.5.2",
    "10.5.3",
    "10.6.0",
    "10.6.1",
    "10.6.2",
    "10.7.0",
    "10.7.1",
    "10.8.0",
)
_LINUX_APP_VERSIONS = (
    "4.14.9 x64",
    "4.15.0 x64",
    "4.15.2 x64",
    "4.16.0 x64",
    "4.16.2 x64",
    "4.16.6 x64",
    "4.16.8 x64",
    "5.0.0 x64",
    "5.0.1 x64",
    "5.1.0 x64",
    "5.2.0 x64",
    "5.2.1 x64",
    "5.3.0 x64",
)
_LANG_CODES = ("en", "ru", "de", "fr", "es", "it", "pt", "ja", "ko", "zh")
_SYSTEM_LANG_CODES = (
    "en-US",
    "en-GB",
    "ru-RU",
    "de-DE",
    "fr-FR",
    "es-ES",
    "it-IT",
    "pt-BR",
    "ja-JP",
    "ko-KR",
    "zh-CN",
    "zh-TW",
    "en-AU",
    "en-CA",
)
_PLATFORMS: tuple[DevicePlatform, ...] = ("windows", "macos", "linux")


def generate_random_device_fingerprint(account_id: str) -> DeviceFingerprint:
    platform = secrets.choice(_PLATFORMS)
    if platform == "windows":
        device_model = secrets.choice(_DESKTOP_DEVICES)
        system_version = secrets.choice(_WINDOWS_VERSIONS)
        app_version = secrets.choice(_WINDOWS_APP_VERSIONS)
    elif platform == "macos":
        device_model = secrets.choice(_MAC_DEVICES)
        system_version = secrets.choice(_MACOS_VERSIONS)
        app_version = secrets.choice(_MAC_APP_VERSIONS)
    else:
        device_model = secrets.choice(_DESKTOP_DEVICES)
        system_version = secrets.choice(_LINUX_DISTROS)
        app_version = secrets.choice(_LINUX_APP_VERSIONS)

    return DeviceFingerprint(
        account_id=account_id,
        platform=platform,
        device_model=device_model,
        system_version=system_version,
        app_version=app_version,
        lang_code=secrets.choice(_LANG_CODES),
        system_lang_code=secrets.choice(_SYSTEM_LANG_CODES),
    )


async def get_or_create_device_fingerprint(account_id: str) -> DeviceFingerprint:
    existing = await fetch_device_fingerprint(account_id)
    if existing is not None:
        return existing

    profile = generate_random_device_fingerprint(account_id)
    return await insert_device_fingerprint(profile)
