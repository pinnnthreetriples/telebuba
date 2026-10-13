"""The proxy-country table: a real zone and a language Chrome and Telethon both speak.

A country outside the table claims ``Etc/UTC`` (``test_web_login_fingerprint``), which
is merely unusual on a desktop; these tests pin that the common proxy countries are in
it, that each one's language is the Telethon device's own for that country whenever
Chrome ships a UI in it, and that the answer never varies between calls.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest

from core.device_fingerprint_lang import LANGUAGE_BY_COUNTRY
from core.web_login import _countries
from core.web_login._countries import COUNTRY
from core.web_login.fingerprint import fingerprint_for


@pytest.mark.parametrize(
    ("country", "timezone", "locale"),
    [
        ("KZ", "Asia/Almaty", "ru-KZ"),
        ("BY", "Europe/Minsk", "ru-BY"),
        ("PT", "Europe/Lisbon", "pt-PT"),
        ("IL", "Asia/Jerusalem", "he-IL"),
        ("MX", "America/Mexico_City", "es-MX"),
        ("ID", "Asia/Jakarta", "id-ID"),
    ],
)
def test_a_common_proxy_country_gets_its_own_zone_and_language(
    country: str, timezone: str, locale: str
) -> None:
    fingerprint = fingerprint_for("acct-1", country.lower())

    assert (fingerprint.timezone, fingerprint.locale) == (timezone, locale)


@pytest.mark.parametrize(("country", "timezone"), [("UZ", "Asia/Tashkent"), ("GE", "Asia/Tbilisi")])
def test_a_language_chrome_has_no_ui_for_falls_back_to_english_not_to_utc(
    country: str, timezone: str
) -> None:
    """The zone still follows the exit IP; only the language becomes an English Chrome's."""
    fingerprint = fingerprint_for("acct-1", country)

    assert LANGUAGE_BY_COUNTRY[country] not in _countries._CHROME_UI_LANGUAGES
    assert (fingerprint.timezone, fingerprint.locale) == (timezone, "en-US")


def test_the_table_covers_about_a_hundred_countries() -> None:
    assert len(COUNTRY) >= 100


def test_every_zone_is_a_real_iana_zone() -> None:
    for zone, _locale in COUNTRY.values():
        assert str(ZoneInfo(zone)) == zone


def test_every_locale_is_the_telethon_language_or_plain_english() -> None:
    """Web and Telethon agree by construction: one language table, never a second one."""
    for country, (_zone, locale) in COUNTRY.items():
        language = LANGUAGE_BY_COUNTRY[country]
        if language in _countries._CHROME_UI_LANGUAGES:
            assert locale == f"{language}-{country}"
        else:
            assert locale == "en-US"


def test_the_same_country_always_resolves_alike() -> None:
    first = [(c, fingerprint_for("acct-1", c)) for c in COUNTRY]
    again = [(c, fingerprint_for("acct-1", c)) for c in COUNTRY]

    assert [(c, f.timezone, f.locale) for c, f in first] == [
        (c, f.timezone, f.locale) for c, f in again
    ]
