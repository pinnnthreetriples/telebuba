from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, cast

from core.db import fetch_account, fetch_device_fingerprint, insert_device_fingerprint
from core.device_fingerprint_lang import language_pair
from schemas.device_fingerprint import DeviceFingerprint, DevicePlatform, DeviceType

if TYPE_CHECKING:
    import random

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
_PLATFORMS: tuple[DevicePlatform, ...] = ("windows", "macos", "linux")

# Each entry holds device_model, system_version, and app_version.
_ANDROID_PROFILES: tuple[tuple[str, str, str], ...] = (
    ("Samsung Galaxy S23 Ultra", "SDK 34 (Android 14)", "Telegram Android 10.9.2"),
    ("Samsung Galaxy S24 Ultra", "SDK 34 (Android 14)", "Telegram Android 10.9.2"),
    ("Google Pixel 8", "SDK 34 (Android 14)", "Telegram Android 10.9.2"),
    ("Xiaomi 13", "SDK 34 (Android 14)", "Telegram Android 10.2"),
    ("Xiaomi 14", "SDK 34 (Android 14)", "Telegram Android 10.9.2"),
)

_IOS_PROFILES: tuple[tuple[str, str, str], ...] = (
    ("iPhone 14 Pro", "17.5", "Telegram iOS 10.2"),
    ("iPhone 15 Pro", "17.5", "Telegram iOS 10.2"),
    ("iPhone 16 Pro", "18.0", "Telegram iOS 10.2"),
)

_MOBILE_WEIGHTS: tuple[float, float] = (0.70, 0.30)
_ANDROID_SHARE_PERCENT = 70


def _choice(
    seq: tuple[str, ...] | tuple[tuple[str, str, str], ...],
    rng: random.Random | None,
) -> str | tuple[str, str, str]:
    return rng.choice(seq) if rng is not None else secrets.choice(seq)


def generate_random_device_fingerprint(
    account_id: str,
    phone: str | None = None,
    *,
    device_type: DeviceType = "desktop",
    rng: random.Random | None = None,
) -> DeviceFingerprint:
    lang_code, system_lang_code = language_pair(phone)
    if device_type == "mobile":
        if rng is not None:
            platform = cast(
                "DevicePlatform",
                rng.choices(("android", "ios"), weights=_MOBILE_WEIGHTS, k=1)[0],
            )
        else:
            platform = "android" if secrets.randbelow(100) < _ANDROID_SHARE_PERCENT else "ios"
        pool = _ANDROID_PROFILES if platform == "android" else _IOS_PROFILES
        device_model, system_version, app_version = cast(
            "tuple[str, str, str]",
            _choice(pool, rng),
        )
        return DeviceFingerprint(
            account_id=account_id,
            device_type="mobile",
            platform=platform,
            device_model=device_model,
            system_version=system_version,
            app_version=app_version,
            lang_code=lang_code,
            system_lang_code=system_lang_code,
        )

    platform = cast("DevicePlatform", _choice(_PLATFORMS, rng))
    if platform == "windows":
        device_model = cast("str", _choice(_DESKTOP_DEVICES, rng))
        system_version = cast("str", _choice(_WINDOWS_VERSIONS, rng))
        app_version = cast("str", _choice(_WINDOWS_APP_VERSIONS, rng))
    elif platform == "macos":
        device_model = cast("str", _choice(_MAC_DEVICES, rng))
        system_version = cast("str", _choice(_MACOS_VERSIONS, rng))
        app_version = cast("str", _choice(_MAC_APP_VERSIONS, rng))
    else:
        device_model = cast("str", _choice(_DESKTOP_DEVICES, rng))
        system_version = cast("str", _choice(_LINUX_DISTROS, rng))
        app_version = cast("str", _choice(_LINUX_APP_VERSIONS, rng))

    return DeviceFingerprint(
        account_id=account_id,
        device_type="desktop",
        platform=platform,
        device_model=device_model,
        system_version=system_version,
        app_version=app_version,
        lang_code=lang_code,
        system_lang_code=system_lang_code,
    )


def generate_fingerprint(
    phone: str | None = None,
    *,
    device_type: DeviceType = "desktop",
    rng: random.Random | None = None,
    account_id: str = "pending",
) -> DeviceFingerprint:
    """Generate a Desktop or Mobile device fingerprint with phone-matched language."""
    return generate_random_device_fingerprint(
        account_id,
        phone=phone,
        device_type=device_type,
        rng=rng,
    )


async def get_or_create_device_fingerprint(
    account_id: str,
    *,
    device_type: DeviceType = "desktop",
) -> DeviceFingerprint:
    existing = await fetch_device_fingerprint(account_id, device_type=device_type)
    if existing is not None:
        return existing

    # Read on the mint path only. On the two import paths the row has no phone
    # yet — the connection that learns it needs this fingerprint first — so they
    # mint the ``en-US`` fallback and the first successful session check
    # corrects the language once (``core.repositories._accounts_session_check``).
    account = await fetch_account(account_id)
    profile = generate_random_device_fingerprint(
        account_id,
        phone=account.phone if account else None,
        device_type=device_type,
    )
    return await insert_device_fingerprint(profile)
