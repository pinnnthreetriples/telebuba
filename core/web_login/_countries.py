"""Proxy country -> the timezone and locale a web window claims there.

Split out of :mod:`core.web_login.fingerprint` for the file-size budget. A country
missing here is left to the fingerprint's ``Etc/UTC`` fallback on purpose (see that
module's docstring): a wrong concrete zone is a contradiction, a zero offset is not.

The zone is :data:`core.country_zones.ZONE_BY_COUNTRY`'s. The language is
:data:`core.device_fingerprint_lang.LANGUAGE_BY_COUNTRY`'s, the table the Telethon
device's ``system_lang_code`` comes from, so the web window and the desktop client agree
by construction. It is used only when Chrome ships its own desktop UI in that language
(:data:`_CHROME_UI_LANGUAGES`): a real Chrome's ``navigator.language`` is its UI locale,
so a language Chrome has no UI for is a window almost no real desktop presents. Such a
country gets ``en-US`` instead, which is what an English Chrome there reports.
"""

from __future__ import annotations

from core.country_zones import ZONE_BY_COUNTRY
from core.device_fingerprint_lang import LANGUAGE_BY_COUNTRY

# Every language the Windows build of Chrome 148 ships its UI in: the ``Locales/*.pak``
# beside chrome.exe, regional variants folded into their language.
_CHROME_UI_LANGUAGES = frozenset(
    {
        "af", "am", "ar", "bg", "bn", "ca", "cs", "da", "de", "el", "en", "es", "et", "fa",
        "fi", "fil", "fr", "gu", "he", "hi", "hr", "hu", "id", "it", "ja", "kn", "ko", "lt",
        "lv", "ml", "mr", "ms", "nb", "nl", "pl", "pt", "ro", "ru", "sk", "sl", "sr", "sv",
        "sw", "ta", "te", "th", "tr", "uk", "ur", "vi", "zh",
    }
)  # fmt: skip
_NO_CHROME_UI = "en-US"


def _locale(country: str) -> str:
    language = LANGUAGE_BY_COUNTRY[country]
    return f"{language}-{country}" if language in _CHROME_UI_LANGUAGES else _NO_CHROME_UI


# country_code -> (IANA timezone, primary BCP-47 locale).
COUNTRY: dict[str, tuple[str, str]] = {
    country: (zone, _locale(country)) for country, zone in ZONE_BY_COUNTRY.items()
}
