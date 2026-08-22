"""The country → language table behind a fingerprint's ``system_lang_code``.

The interesting failure mode here is not "the code throws" but "the code is
coherent and wrong": a table with JP and KR swapped, or with half the rows
deleted, still produces a well-formed ``ll-CC`` tag for every account. So the
per-country tests below pin exact tags rather than shapes, and the sweeps assert
over every region ``phonenumbers`` can return rather than over a sample.
"""

from __future__ import annotations

import phonenumbers
import pytest

from core.device_fingerprint_lang import (
    FALLBACK_TAG,
    LANGUAGE_BY_COUNTRY,
    language_pair,
)
from core.phone_geo import _lang_region

# One valid national number per country, from ``phonenumbers.example_number``.
# Regions sharing a calling code are deliberately present in pairs — US/CA on
# +1, RU/KZ on +7, CN/TW on +886/+86 — because a table that collapses either
# member onto the other's tag is exactly the mutation that survived before.
_PINNED_TAGS = [
    ("+12015550123", "en", "en-US"),
    ("+15062345678", "en", "en-CA"),
    ("+441212345678", "en", "en-GB"),
    ("+61212345678", "en", "en-AU"),
    ("+73011234567", "ru", "ru-RU"),
    ("+77123456789", "ru", "ru-KZ"),
    ("+375152450911", "ru", "ru-BY"),
    ("+380311234567", "uk", "uk-UA"),
    ("+4930123456", "de", "de-DE"),
    ("+33123456789", "fr", "fr-FR"),
    ("+34810123456", "es", "es-ES"),
    ("+390212345678", "it", "it-IT"),
    ("+551123456789", "pt", "pt-BR"),
    ("+81312345678", "ja", "ja-JP"),
    ("+8222123456", "ko", "ko-KR"),
    ("+861012345678", "zh", "zh-CN"),
    ("+886221234567", "zh", "zh-TW"),
    ("+48123456789", "pl", "pl-PL"),
    ("+902123456789", "tr", "tr-TR"),
    ("+917410410123", "hi", "hi-IN"),
]


@pytest.mark.parametrize(("phone", "lang", "tag"), _PINNED_TAGS)
def test_each_country_gets_its_own_pinned_tag(phone: str, lang: str, tag: str) -> None:
    assert language_pair(phone) == (lang, tag)


def test_table_covers_every_region_phonenumbers_can_return() -> None:
    """Full coverage is the point: the fallback must mean "no phone", not "no row".

    Without this the table can silently regress to ``en-US`` for a real country
    — which is the bug that shipped when the keys were whatever regions happened
    to appear in a hand-written tag list (14 of 245).
    """
    assert set(LANGUAGE_BY_COUNTRY) == set(phonenumbers.SUPPORTED_REGIONS)


@pytest.mark.parametrize("country", sorted(phonenumbers.SUPPORTED_REGIONS))
def test_every_row_is_a_well_formed_lowercase_two_letter_subtag(country: str) -> None:
    language = LANGUAGE_BY_COUNTRY[country]

    assert len(language) == 2
    assert language.isascii()
    assert language.isalpha()
    assert language == language.lower()


@pytest.mark.parametrize("country", sorted(phonenumbers.SUPPORTED_REGIONS))
def test_assembled_tag_reports_its_own_country_back(country: str) -> None:
    """``lang_matches`` holds for every country because the tag is assembled.

    ``_lang_region`` is the function ``evaluate_geo`` uses to read the region out
    of a tag, so inverting it here is the same question the consumer asks.
    """
    tag = f"{LANGUAGE_BY_COUNTRY[country]}-{country}"

    assert _lang_region(tag) == country


def test_telegram_canonical_and_bare_digit_forms_agree() -> None:
    """Telegram returns ``me.phone`` as bare E.164 digits, with no ``+``.

    ``phone_geo._parse`` restores the ``+``, so the language correction driven by
    a session-check result lands on the same tag as an operator-typed number —
    this is what lets the correction repair an unparseable typed number without
    any normalisation of its own.
    """
    assert language_pair("79161234567") == language_pair("+79161234567") == ("ru", "ru-RU")


def test_fallback_covers_only_a_missing_or_unusable_number() -> None:
    """With full region coverage the fallback means "no phone yet".

    The remaining ways in: no number at all (both import paths, before the first
    connection), an operator-typed number no parser can read, and a
    non-geographic number whose region code is ``001`` rather than a territory.
    """
    fallback = (FALLBACK_TAG.split("-")[0], FALLBACK_TAG)

    assert language_pair(None) == fallback
    assert language_pair("8 916 123 45 67") == fallback
    assert language_pair("+800123456789") == fallback
