"""Add a second device to an account: its Telethon sign-in, then, for a phone, its pod.

``devices`` signs the device in and records it; this module sits above both it and
``mobile_emulator`` (which imports ``devices`` for its refusals and code pacing), so
neither imports the other back.

With the emulator on, a new mobile device's pod is made ready (first boot, seeding,
the APK) in the background, without signing the app in: that sign-in asks Telegram
for another login code, and the one just spent still holds the pacing, so two
requests seconds apart would earn "too many attempts". The operator's own open signs
the app in once the pause allows. The device is recorded before the pod is touched:
a pod that fails answers its own code and the device stays, to be opened again.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.config import settings
from services.accounts.devices import sign_in_account_device
from services.accounts.mobile_emulator import open_account_mobile

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.device_fingerprint import DeviceType

__all__ = ["add_account_device"]


async def add_account_device(account_id: str, device_type: DeviceType) -> AccountRead:
    """Sign ``device_type`` in as the account's second authorization; ready a phone's pod.

    Only by the operator's explicit request: nothing calls this on its own.
    """
    account = await sign_in_account_device(account_id, device_type)
    if device_type == "mobile" and settings.emulator.enabled:
        await open_account_mobile(account_id, background=True)
    return account
