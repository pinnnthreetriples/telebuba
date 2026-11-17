"""T8 — the per-language lexicon structure: en and ru as shipped, a fixture for each language."""

from __future__ import annotations

import pytest

from schemas.spam_status import SpamStatusProbe
from services._spam_lexicon import (
    AUTOMATED_MARKERS,
    BEING_CHECKED_MARKERS,
    CLEAN_MARKERS,
    LEXICONS,
    LIMITED_MARKERS,
    language_of,
)
from services._spam_parse import CHECKING_DETAIL, classify_legacy, classify_strict, find_date
from tests.services.spam_intel._reply_fixtures import (
    CLEAN_RU_SHORT,
    FIXTURES,
    UNREAD_ES,
    UNREAD_ES_LIMITED,
)


def _probe(text: str) -> SpamStatusProbe:
    return SpamStatusProbe(account_id="a", reply_text=text)


def test_the_lexicons_are_exactly_the_markers_the_legacy_parser_shipped_with() -> None:
    """En and ru unchanged: nothing is added, reordered or dropped (not invented)."""
    assert [lexicon.lang for lexicon in LEXICONS] == ["en", "ru"]
    assert CLEAN_MARKERS == (
        "no limits",
        "good news",
        "хорошие новости",
        "не применены",
        "свободен от",
    )
    assert BEING_CHECKED_MARKERS == (
        "being checked",
        "is checked",
        "сейчас проверя",
        "автоматизирован",
    )
    assert LIMITED_MARKERS == ("limited", "restricted", "наложены")
    assert AUTOMATED_MARKERS == ("automat", "автомат")


def test_every_lexicon_is_well_formed() -> None:
    tags = [lexicon.lang for lexicon in LEXICONS]
    assert len(tags) == len(set(tags))
    for lexicon in LEXICONS:
        everything = (*lexicon.clean, *lexicon.being_checked, *lexicon.limited, *lexicon.automated)
        assert lexicon.clean
        assert lexicon.limited
        assert all(marker == marker.lower() and marker.strip() for marker in everything)


def test_every_lexicon_has_fixtures() -> None:
    """Adding a language is one data entry plus one fixture: a lexicon without one fails here."""
    assert {lexicon.lang for lexicon in LEXICONS} == set(FIXTURES)


@pytest.mark.parametrize("lang", sorted(FIXTURES))
def test_each_language_fixture_reads_as_labelled_in_both_classifiers(lang: str) -> None:
    fixtures = FIXTURES[lang]
    for classify in (classify_legacy, classify_strict):
        assert classify(_probe(fixtures.clean)) == ("clean", None)
        assert classify(_probe(fixtures.limited_dated))[0] == "limited"
        assert classify(_probe(fixtures.being_checked)) == ("unknown", CHECKING_DETAIL)
    assert find_date(fixtures.limited_dated) is not None
    assert find_date(fixtures.clean) is None


@pytest.mark.parametrize("lang", sorted(FIXTURES))
def test_the_language_tag_follows_the_text(lang: str) -> None:
    fixtures = FIXTURES[lang]
    assert {
        language_of(fixtures.clean),
        language_of(fixtures.limited_dated),
        language_of(fixtures.being_checked),
    } == {lang}


def test_no_fixture_matches_the_opposite_verdict_of_its_own_language() -> None:
    """A clean reply must carry no limited marker and the reverse, or strict calls it ambiguous."""
    for fixtures in FIXTURES.values():
        assert not any(m in fixtures.clean.lower() for m in LIMITED_MARKERS)
        assert not any(m in fixtures.limited_dated.lower() for m in CLEAN_MARKERS)


def test_the_short_russian_clean_form_is_not_read_as_a_limit() -> None:
    assert classify_strict(_probe(CLEAN_RU_SHORT)) == ("clean", None)
    assert language_of(CLEAN_RU_SHORT) == "ru"


def test_a_language_without_a_lexicon_has_no_tag_and_stays_unreadable() -> None:
    """es/pt/fr and the rest wait for captured samples: unknown to strict, undated to legacy."""
    for text in (UNREAD_ES, UNREAD_ES_LIMITED):
        assert language_of(text) is None
        assert classify_strict(_probe(text)) == ("unknown", "unrecognized_reply")
        assert classify_legacy(_probe(text)) == ("clean", None)


def test_the_structural_date_read_needs_no_lexicon() -> None:
    dated = "Votre compte reste bloqué jusqu'au 12 juillet 2026."
    assert language_of(dated) is None
    assert classify_strict(_probe(dated))[0] == "limited"
