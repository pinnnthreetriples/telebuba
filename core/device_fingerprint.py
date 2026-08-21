from __future__ import annotations

import secrets

from core.db import fetch_account, fetch_device_fingerprint, insert_device_fingerprint
from core.phone_geo import _lang_region, country_for_phone
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

# The region halves of the tags above ARE the countries we can dress an account
# for, so they are grouped, not typed out a second time. ``_lang_region`` is the
# very function ``phone_geo.evaluate_geo`` uses to read the region back out of a
# tag; inverting it here is what keeps the generator and that consumer from ever
# disagreeing about which country ``ru-RU`` claims.
_TAG_BY_COUNTRY: dict[str, str] = {
    region: tag for tag in _SYSTEM_LANG_CODES if (region := _lang_region(tag))
}
_FALLBACK_TAG = "en-US"


def _language_pair(phone: str | None) -> tuple[str, str]:
    """``(lang_code, system_lang_code)`` coherent with the phone's country.

    Telegram sees both fields. Drawing them independently let one account
    announce ``lang_code="en"`` beside ``system_lang_code="ko-KR"`` on a Russian
    number, which no real Telegram Desktop install does. Here the regional tag
    follows the phone country and the bare language is that tag's own first
    half, so the two cannot contradict each other.

    The phone is the only input available: the fingerprint is minted at account
    creation (``services.accounts.lifecycle.add_account``) BEFORE any proxy is
    assigned, so there is no proxy country to consult at this point — plumbing
    one in would read an association that does not exist yet.

    With no phone, or a country outside the tag set, the pair falls back to
    ``en-US`` rather than a random draw: an unrecognised OS locale is exactly
    when a real Telegram Desktop settles on English, and a random draw is the
    bug being fixed — it is what let the tag contradict the number.
    """
    tag = _TAG_BY_COUNTRY.get(country_for_phone(phone) or "", _FALLBACK_TAG)
    return tag.split("-", 1)[0], tag


def generate_random_device_fingerprint(
    account_id: str,
    phone: str | None = None,
) -> DeviceFingerprint:
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

    lang_code, system_lang_code = _language_pair(phone)
    return DeviceFingerprint(
        account_id=account_id,
        platform=platform,
        device_model=device_model,
        system_version=system_version,
        app_version=app_version,
        lang_code=lang_code,
        system_lang_code=system_lang_code,
    )


async def get_or_create_device_fingerprint(account_id: str) -> DeviceFingerprint:
    existing = await fetch_device_fingerprint(account_id)
    if existing is not None:
        return existing

    # Read on the mint path only: an existing fingerprint is immutable, so an
    # account whose phone lands later keeps the language it was born with.
    account = await fetch_account(account_id)
    profile = generate_random_device_fingerprint(
        account_id,
        phone=account.phone if account else None,
    )
    return await insert_device_fingerprint(profile)
