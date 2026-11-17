"""Which Telegram API (``api_id`` / ``api_hash``) a session uses: the one that made its key.

Why: every session Telebuba creates shows the registered app's title in the account's
session list, and the old title is "userbot01". A neutral app can be registered, but an
existing key must not start connecting with another API: Telethon allows it and Telegram
accepts it, yet tools that know the subject warn it gets accounts banned (opentele: "Don't
use a same session with multiple different APIs"), and nothing documents it as safe.

So the API is recorded per session (``device_fingerprints.api_id``) and never changes:

* ``None`` (every session from before this, and every import): the configured
  ``TELEGRAM__API_ID`` (desktop) or ``TELEGRAM__MOBILE_API_ID`` (mobile), as always;
* the id of ``TELEGRAM__NEW_API_ID``: that API, and only while its hash is configured.

Only a NEW key gets the new API (``TelegramClientRequest.new_key``): a re-key's new session,
a second device, and a sign-in that re-authorises a key Telegram already ended. The id is
recorded once the sign-in succeeded. Anything else (a recorded API whose credentials are
gone, an unknown id) is refused rather than silently connected with another one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.config import settings
from core.repositories.device_fingerprint import set_device_api_id

if TYPE_CHECKING:
    from schemas.device_fingerprint import DeviceFingerprint, TelegramClientProfile

__all__ = [
    "ApiCredentialsMissingError",
    "api_credentials",
    "new_api_id",
    "record_key_api",
]


class ApiCredentialsMissingError(ConnectionError):
    """The API this session's key was made with is not configured: it is not connected."""


def new_api_id() -> int | None:
    """The new desktop API's id when it is fully configured, else ``None``."""
    telegram = settings.telegram
    return telegram.new_api_id if telegram.new_api_id and telegram.new_api_hash else None


def api_credentials(device: DeviceFingerprint) -> tuple[int, str]:
    """The ``(api_id, api_hash)`` this device's session connects with; see the module doc."""
    telegram = settings.telegram
    if device.api_id is None:
        if device.device_type == "mobile":
            return telegram.mobile_api_id, telegram.mobile_api_hash
        return telegram.api_id, telegram.api_hash
    if device.api_id == telegram.new_api_id and telegram.new_api_hash:
        return telegram.new_api_id, telegram.new_api_hash
    if device.api_id == telegram.api_id and device.device_type == "desktop":
        return telegram.api_id, telegram.api_hash
    msg = "the API this session was created with is not configured"
    raise ApiCredentialsMissingError(msg)


async def record_key_api(profile: TelegramClientProfile) -> None:
    """After a sign-in made the key: remember a NEW API on the device's row, so it stays.

    A profile on the default API records nothing (``None`` already means the default).
    """
    device = getattr(profile, "device", None)
    if device is not None and device.api_id is not None:
        await set_device_api_id(profile.account_id, device.device_type, device.api_id)
