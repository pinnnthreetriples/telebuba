"""The Android catalogue a mobile authorization's fingerprint is drawn from.

Every field is what the official Telegram Android app itself sends in
``initConnection`` (``org.telegram.tgnet.ConnectionsManager``):

* ``device_model`` = ``Build.MANUFACTURER + Build.MODEL``, concatenated with no
  separator — ``samsungSM-S918B``, ``GooglePixel 8``;
* ``system_version`` = ``"SDK " + Build.VERSION.SDK_INT``;
* ``app_version`` = ``versionName + " (" + versionCode + ")"``.

A device is listed only with the Android releases it really ran (shipped with or
was updated to), and only Android 13/14 (SDK 33/34): those are the releases the
configured redroid images offer, and the emulator refuses to sign in when its real
SDK differs from the account's fingerprint. Every device and release here has a real build in
``configs/oem_blueprints`` that the container presents. App versions are official releases with
their build numbers as published by Telegram's own APK channel (t.me/TAndroidAPK).

The fingerprint is immutable, so an account keeps the app version it was minted
with; refresh this list for new accounts, never rewrite stored rows.
"""

from __future__ import annotations

import secrets
from typing import NamedTuple

from core.config import settings
from core.device_fingerprint_lang import language_pair
from schemas.device_fingerprint import DeviceFingerprint


class AndroidDevice(NamedTuple):
    manufacturer: str  # ``Build.MANUFACTURER``, exact case
    model: str  # ``Build.MODEL``
    sdk_levels: tuple[int, ...]
    name: str  # what the phone is sold as, for the operator's eyes only


ANDROID_DEVICES: tuple[AndroidDevice, ...] = (
    AndroidDevice("samsung", "SM-S911B", (33, 34), "Galaxy S23"),
    AndroidDevice("samsung", "SM-S918B", (33, 34), "Galaxy S23 Ultra"),
    AndroidDevice("samsung", "SM-A546B", (33, 34), "Galaxy A54 5G"),
    AndroidDevice("samsung", "SM-S921B", (34,), "Galaxy S24"),
    AndroidDevice("samsung", "SM-A556B", (34,), "Galaxy A55 5G"),
    AndroidDevice("Google", "Pixel 7", (33, 34), "Pixel 7"),
    AndroidDevice("Google", "Pixel 8", (34,), "Pixel 8"),
    AndroidDevice("Xiaomi", "2211133G", (33, 34), "Xiaomi 13"),
    AndroidDevice("Xiaomi", "23049PCD8G", (33,), "POCO F5"),
    AndroidDevice("Xiaomi", "22101316G", (33,), "Redmi Note 12 Pro 5G"),
    AndroidDevice("OnePlus", "CPH2449", (33,), "OnePlus 11"),
)

ANDROID_APP_VERSIONS: tuple[str, ...] = (
    "12.9.1 (6979)",
    "12.9.2 (6991)",
    "12.10.0 (7031)",
    "12.10.1 (7038)",
    "12.10.2 (7086)",
    "12.10.3 (7089)",
    "12.10.4 (7099)",
    "12.10.5 (7105)",
    "12.10.6 (7112)",
)

# What Telegram Android sends as ``initConnection.lang_pack``.
ANDROID_LANG_PACK = "android"


def android_device_model(device: AndroidDevice) -> str:
    return f"{device.manufacturer}{device.model}"


def android_app_version() -> str:
    """The configured APK's version when there is one, else one from the list.

    Only new fingerprints use it: a stored one keeps the version it was minted with.
    """
    return settings.emulator.telegram_app_version or secrets.choice(ANDROID_APP_VERSIONS)


def android_device_for(device_model: str) -> AndroidDevice | None:
    """The catalogue phone a fingerprint's ``device_model`` names, if it is one."""
    return next((d for d in ANDROID_DEVICES if android_device_model(d) == device_model), None)


def generate_android_fingerprint(account_id: str, phone: str | None) -> DeviceFingerprint:
    """A fresh, internally consistent Android fingerprint for a mobile authorization."""
    device = secrets.choice(ANDROID_DEVICES)
    lang_code, system_lang_code = language_pair(phone)
    return DeviceFingerprint(
        account_id=account_id,
        device_type="mobile",
        platform="android",
        device_model=android_device_model(device),
        system_version=f"SDK {secrets.choice(device.sdk_levels)}",
        app_version=android_app_version(),
        lang_code=lang_code,
        system_lang_code=system_lang_code,
    )
