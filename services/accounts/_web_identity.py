"""The identity an account's web window wears, agreed with its Telethon device.

Split out of :mod:`services.accounts.web_login` for the file-size budget.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.db import fetch_device_fingerprint
from core.logging import log_event
from core.web_login import fingerprint_for
from core.web_login.fingerprint import LocaleRejectedError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from core.web_login.browser import WebWindow
    from core.web_login.fingerprint import Fingerprint

# Locales this run's browser has refused. A property of the installed Chrome build, not
# of an account, so every later window skips straight to the proxy country's language
# instead of paying a failed browser start first.
_rejected_locales: set[str] = set()


async def web_fingerprint(account_id: str, country: str | None) -> Fingerprint:
    """The account's web fingerprint: its device's language, its proxy's timezone.

    Telegram already hears the account's Telethon device announce a language
    (``system_lang_code``, from the phone number); a web session speaking the proxy
    country's language instead puts two on one account. The device read is the
    account's PRIMARY one — desktop whenever it has a desktop session, the mobile one
    only for a mobile-only account — which is the device a web window sits beside. An
    account with no device row yet has announced none, so the proxy country decides
    both, as before. Read, never minted: creating the row is the Telethon connection's
    business.
    """
    device = await fetch_device_fingerprint(account_id)
    language = device.system_lang_code if device else None
    if language in _rejected_locales:
        language = None
    return fingerprint_for(account_id, country, language)


async def launch_dressed(
    account_id: str,
    country: str | None,
    launch: Callable[[Fingerprint], Awaitable[WebWindow]],
) -> WebWindow:
    """Launch the account's window; in the proxy country's language if Chrome refuses.

    A device language the browser does not know must not keep the account out of its
    window. The refused window is already gone (the launcher kills it), so this starts
    a fresh one in a locale Chrome knows, and logs the fallback by code only.
    """
    fingerprint = await web_fingerprint(account_id, country)
    try:
        return await launch(fingerprint)
    except LocaleRejectedError:
        fallback = fingerprint_for(account_id, country)
        if fallback.locale == fingerprint.locale:
            raise
        _rejected_locales.add(fingerprint.locale)
        await log_event("WARNING", "web_login_locale_rejected", account_id=account_id)
        return await launch(fallback)
