"""Proxy country -> the timezone and locale a web window claims there.

Split out of :mod:`core.web_login.fingerprint` for the file-size budget. A country
missing here is left to the fingerprint's ``Etc/UTC`` fallback on purpose (see that
module's docstring): a wrong concrete zone is a contradiction, a zero offset is not.

The zone is the country's most populous one. The language is
:data:`core.device_fingerprint_lang.LANGUAGE_BY_COUNTRY`'s, the table the Telethon
device's ``system_lang_code`` comes from, so the web window and the desktop client agree
by construction. It is used only when Chrome ships its own desktop UI in that language
(:data:`_CHROME_UI_LANGUAGES`): a real Chrome's ``navigator.language`` is its UI locale,
so a language Chrome has no UI for is a window almost no real desktop presents. Such a
country gets ``en-US`` instead, which is what an English Chrome there reports.
"""

from __future__ import annotations

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

_ZONE_BY_COUNTRY: dict[str, str] = {
    # CIS and the Caucasus
    "RU": "Europe/Moscow",
    "UA": "Europe/Kyiv",
    "BY": "Europe/Minsk",
    "KZ": "Asia/Almaty",
    "UZ": "Asia/Tashkent",
    "KG": "Asia/Bishkek",
    "TJ": "Asia/Dushanbe",
    "TM": "Asia/Ashgabat",
    "AZ": "Asia/Baku",
    "AM": "Asia/Yerevan",
    "GE": "Asia/Tbilisi",
    "MD": "Europe/Chisinau",
    # Europe
    "EE": "Europe/Tallinn",
    "LV": "Europe/Riga",
    "LT": "Europe/Vilnius",
    "PL": "Europe/Warsaw",
    "CZ": "Europe/Prague",
    "SK": "Europe/Bratislava",
    "HU": "Europe/Budapest",
    "RO": "Europe/Bucharest",
    "BG": "Europe/Sofia",
    "GR": "Europe/Athens",
    "CY": "Asia/Nicosia",
    "HR": "Europe/Zagreb",
    "SI": "Europe/Ljubljana",
    "RS": "Europe/Belgrade",
    "BA": "Europe/Sarajevo",
    "ME": "Europe/Podgorica",
    "MK": "Europe/Skopje",
    "AL": "Europe/Tirane",
    "DE": "Europe/Berlin",
    "AT": "Europe/Vienna",
    "CH": "Europe/Zurich",
    "LI": "Europe/Vaduz",
    "NL": "Europe/Amsterdam",
    "BE": "Europe/Brussels",
    "LU": "Europe/Luxembourg",
    "FR": "Europe/Paris",
    "MC": "Europe/Monaco",
    "GB": "Europe/London",
    "IE": "Europe/Dublin",
    "ES": "Europe/Madrid",
    "PT": "Europe/Lisbon",
    "IT": "Europe/Rome",
    "MT": "Europe/Malta",
    "DK": "Europe/Copenhagen",
    "NO": "Europe/Oslo",
    "SE": "Europe/Stockholm",
    "FI": "Europe/Helsinki",
    "IS": "Atlantic/Reykjavik",
    "TR": "Europe/Istanbul",
    # Middle East and North Africa
    "IL": "Asia/Jerusalem",
    "AE": "Asia/Dubai",
    "SA": "Asia/Riyadh",
    "QA": "Asia/Qatar",
    "KW": "Asia/Kuwait",
    "BH": "Asia/Bahrain",
    "OM": "Asia/Muscat",
    "JO": "Asia/Amman",
    "LB": "Asia/Beirut",
    "IQ": "Asia/Baghdad",
    "IR": "Asia/Tehran",
    "EG": "Africa/Cairo",
    "MA": "Africa/Casablanca",
    "DZ": "Africa/Algiers",
    "TN": "Africa/Tunis",
    # Asia and Oceania
    "IN": "Asia/Kolkata",
    "PK": "Asia/Karachi",
    "BD": "Asia/Dhaka",
    "LK": "Asia/Colombo",
    "NP": "Asia/Kathmandu",
    "CN": "Asia/Shanghai",
    "HK": "Asia/Hong_Kong",
    "TW": "Asia/Taipei",
    "JP": "Asia/Tokyo",
    "KR": "Asia/Seoul",
    "MN": "Asia/Ulaanbaatar",
    "SG": "Asia/Singapore",
    "MY": "Asia/Kuala_Lumpur",
    "ID": "Asia/Jakarta",
    "TH": "Asia/Bangkok",
    "VN": "Asia/Ho_Chi_Minh",
    "PH": "Asia/Manila",
    "KH": "Asia/Phnom_Penh",
    "MM": "Asia/Yangon",
    "AU": "Australia/Sydney",
    "NZ": "Pacific/Auckland",
    # Americas
    "US": "America/New_York",
    "CA": "America/Toronto",
    "MX": "America/Mexico_City",
    "BR": "America/Sao_Paulo",
    "AR": "America/Argentina/Buenos_Aires",
    "CL": "America/Santiago",
    "CO": "America/Bogota",
    "PE": "America/Lima",
    "VE": "America/Caracas",
    "EC": "America/Guayaquil",
    "BO": "America/La_Paz",
    "PY": "America/Asuncion",
    "UY": "America/Montevideo",
    "CR": "America/Costa_Rica",
    "PA": "America/Panama",
    "GT": "America/Guatemala",
    "DO": "America/Santo_Domingo",
    "PR": "America/Puerto_Rico",
    "JM": "America/Jamaica",
    "HN": "America/Tegucigalpa",
    "SV": "America/El_Salvador",
    # Sub-Saharan Africa
    "ZA": "Africa/Johannesburg",
    "NG": "Africa/Lagos",
    "KE": "Africa/Nairobi",
    "GH": "Africa/Accra",
    "ET": "Africa/Addis_Ababa",
    "TZ": "Africa/Dar_es_Salaam",
    "CI": "Africa/Abidjan",
    "SN": "Africa/Dakar",
    "CM": "Africa/Douala",
}


def _locale(country: str) -> str:
    language = LANGUAGE_BY_COUNTRY[country]
    return f"{language}-{country}" if language in _CHROME_UI_LANGUAGES else _NO_CHROME_UI


# country_code -> (IANA timezone, primary BCP-47 locale).
COUNTRY: dict[str, tuple[str, str]] = {
    country: (zone, _locale(country)) for country, zone in _ZONE_BY_COUNTRY.items()
}
