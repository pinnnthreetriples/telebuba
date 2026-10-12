"""The identity an account's web window wears, agreed with its Telethon device.

Split out of :mod:`services.accounts.web_login` for the file-size budget.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.db import fetch_device_fingerprint
from core.web_login import fingerprint_for

if TYPE_CHECKING:
    from core.web_login.fingerprint import Fingerprint


async def web_fingerprint(account_id: str, country: str | None) -> Fingerprint:
    """The account's web fingerprint: its device's language, its proxy's timezone.

    Telegram already hears the account's Telethon device announce a language
    (``system_lang_code``, from the phone number); a web session speaking the proxy
    country's language instead puts two on one account. An account with no device row
    yet has announced none, so the proxy country decides both, as before. Read, never
    minted: creating the device row is the Telethon connection's business.
    """
    device = await fetch_device_fingerprint(account_id)
    return fingerprint_for(account_id, country, device.system_lang_code if device else None)
