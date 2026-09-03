"""Category bundles: one per request code, every word a legal keyword."""

from __future__ import annotations

from typing import get_args

from schemas.neurocomment_discovery_request import (
    KEYWORD_MAX_LENGTH,
    KEYWORD_MIN_LENGTH,
    DiscoveryCategory,
)
from services.neurocomment._discovery_categories import BUNDLES, keywords_for, matches


def test_every_category_code_has_a_bundle_and_nothing_else_does() -> None:
    assert set(get_args(DiscoveryCategory)) - {"any"} == set(BUNDLES)


def test_every_bundle_word_passes_the_keyword_validator() -> None:
    """The bundle rides the same request as typed keywords, so its bounds are theirs."""
    for code, words in BUNDLES.items():
        assert 6 <= len(words) <= 10, code
        for word in words:
            assert KEYWORD_MIN_LENGTH <= len(word) <= KEYWORD_MAX_LENGTH, (code, word)
            assert word == word.strip().casefold(), (code, word)


def test_keywords_for_is_empty_for_any_and_for_an_unknown_code() -> None:
    assert keywords_for("any") == []
    assert keywords_for("cats") == []
    assert keywords_for("crypto") == list(BUNDLES["crypto"])


def test_matches_is_a_case_insensitive_substring_over_title_and_about() -> None:
    assert matches("Всё о Биткоине", None, "crypto")
    assert matches("Daily digest", "DeFi yields and more", "crypto")
    assert not matches("Кулинария", "рецепты", "crypto")
    # ``any`` is not a filter.
    assert matches("", None, "any")
    # An unknown code has no words, so it matches nothing rather than everything.
    assert not matches("crypto", "crypto", "cats")
