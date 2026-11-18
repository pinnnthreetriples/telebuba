"""The two reply classifiers and the redaction of stored samples.

The leak classes of redaction are in ``tests/services/spam_intel/test_samples_redaction.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from schemas.spam_status import SpamStatusProbe
from services._spam_parse import (
    AMBIGUOUS_DETAIL,
    UNRECOGNIZED_DETAIL,
    classify_legacy,
    classify_strict,
    extract_until,
    find_date,
)
from services._spam_redact import redact_reply, reply_hash

CLEAN_EN = "Good news, no limits are currently applied to your account. You're free as a bird!"
CLEAN_RU = "Хорошие новости, на ваш аккаунт не применены никакие ограничения."
CLEAN_RU_SHORT = "Ваш аккаунт свободен от каких-либо ограничений."
LIMITED_EN = "Your account is now limited until 29 Oct 2026, 11:44 UTC."
LIMITED_RU = "На ваш аккаунт наложены ограничения до 29 окт. 2026."
CHECKING_EN = "Your account is being checked."
CHECKING_RU = "Ваш аккаунт сейчас проверяется автоматизированной системой."
SPANISH_UNKNOWN = "Tu cuenta no tiene ninguna restricción en este momento, ¡eres libre!"
SPANISH_LIMITED_NO_DATE = "Lamentablemente, tu cuenta está limitada de forma indefinida."
GERMAN_LIMITED_DATED = "Dein Account ist derzeit eingeschränkt bis 12. Juli 2026."


def _probe(text: str | None, **fields: Any) -> SpamStatusProbe:
    return SpamStatusProbe.model_validate({"account_id": "a", "reply_text": text, **fields})


@pytest.mark.parametrize(
    ("text", "status"),
    [
        (CLEAN_EN, "clean"),
        (CLEAN_RU, "clean"),
        (CLEAN_RU_SHORT, "clean"),
        (LIMITED_EN, "limited"),
        (LIMITED_RU, "limited"),
        (CHECKING_EN, "unknown"),
        (CHECKING_RU, "unknown"),
    ],
)
def test_both_classifiers_agree_on_every_text_the_markers_know(text: str, status: str) -> None:
    assert classify_legacy(_probe(text))[0] == status
    assert classify_strict(_probe(text))[0] == status


def test_the_legacy_fallback_reads_an_unknown_language_as_clean() -> None:
    """The bug the strict classifier exists to remove: no marker, no date, so "clean"."""
    assert classify_legacy(_probe(SPANISH_LIMITED_NO_DATE)) == ("clean", None)
    assert classify_legacy(_probe(SPANISH_UNKNOWN)) == ("clean", None)


def test_the_strict_classifier_never_says_clean_for_a_text_it_did_not_recognise() -> None:
    for text in (SPANISH_LIMITED_NO_DATE, SPANISH_UNKNOWN, "Lorem ipsum dolor sit amet."):
        assert classify_strict(_probe(text)) == ("unknown", UNRECOGNIZED_DETAIL)


def test_a_date_marks_an_unknown_language_limited_in_both_classifiers() -> None:
    assert classify_legacy(_probe(GERMAN_LIMITED_DATED))[0] == "limited"
    status, detail = classify_strict(_probe(GERMAN_LIMITED_DATED))
    assert (status, detail) == ("limited", "12. Juli 2026")


def test_a_reply_with_both_a_clean_and_a_limited_marker_is_ambiguous_in_both() -> None:
    """Pins the audit's F-013: the legacy classifier used to answer ``clean`` for this text."""
    text = "Good news: the account that was limited yesterday has no limits now."
    assert classify_legacy(_probe(text)) == ("unknown", AMBIGUOUS_DETAIL)
    assert classify_strict(_probe(text)) == ("unknown", AMBIGUOUS_DETAIL)


def test_a_probe_error_and_an_empty_reply_are_unknown_in_both() -> None:
    for classify in (classify_legacy, classify_strict):
        assert classify(SpamStatusProbe(account_id="a", error="TimeoutError")) == (
            "unknown",
            "TimeoutError",
        )
        assert classify(_probe("   "))[0] == "unknown"
        assert classify(_probe(None))[0] == "unknown"


def test_the_restricted_flag_limits_neither_classifier() -> None:
    """Telegram documents ``restricted`` as a platform restriction, not a spam limit.

    Pins the audit's F-004: the legacy classifier used to turn the flag into ``limited``.
    """
    probe = _probe("something unrelated", restricted=True, restriction_reason="terms")
    assert classify_legacy(probe) == ("clean", None)  # the legacy fallback, flag or no flag
    assert classify_strict(probe) == ("unknown", UNRECOGNIZED_DETAIL)
    clean = _probe(CLEAN_EN, restricted=True)
    assert classify_strict(clean)[0] == "clean"


def test_a_dated_limit_carries_the_until_tail_or_the_bare_date() -> None:
    assert classify_strict(_probe(LIMITED_EN))[1] == "until 29 Oct 2026, 11:44 UTC"
    assert extract_until("Hasta el 12 de julio de 2026") == "12 de julio de 2026"
    assert extract_until("no date here") is None


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("until 12.07.2026", "12.07.2026"),
        ("until 2026-07-12", "2026-07-12"),
        ("до 12 июля 2026", "12 июля 2026"),
        ("sin fecha", None),
    ],
)
def test_find_date_covers_the_numeric_iso_and_named_month_forms(
    text: str, found: str | None
) -> None:
    assert find_date(text) == found


def test_redaction_removes_every_name_the_account_carries() -> None:
    text = (
        "Hello María García (@maria_g), your number +34 612 345 678 is limited until 29 Oct 2026."
    )
    redacted = redact_reply(text, ["María", "García", "maria_g", "+34612345678", None], 1000)
    assert "María" not in redacted
    assert "García" not in redacted
    assert "maria_g" not in redacted
    assert "612" not in redacted
    assert "<name>" in redacted
    assert "29 Oct 2026" in redacted  # the date is the part the fixtures need


def test_redaction_masks_emails_phones_and_long_digit_runs_but_keeps_dates() -> None:
    text = "Write to help@example.com, id 123456789, tel +1 (555) 010-9999, until 12.07.2026"
    redacted = redact_reply(text, [], 1000)
    assert "<email>" in redacted
    assert "<num>" in redacted
    assert "<phone>" in redacted
    assert "12.07.2026" in redacted
    assert "123456789" not in redacted
    assert "example.com" not in redacted


def test_redaction_is_bounded_and_collapses_blank_runs() -> None:
    redacted = redact_reply("a" * 5000 + "\n\n\n\n\nb", [], 1000)
    assert len(redacted) == 1000
    assert "\n\n\n" not in redact_reply("x\n\n\n\n\ny", [], 1000)


def test_a_one_letter_name_is_masked_as_a_whole_token_only() -> None:
    """Pins the audit's F-001: a one-character first name used to survive redaction."""
    assert redact_reply("A bad account, Abe", ["A"], 1000) == "<name> bad account, Abe"


def test_the_hash_ignores_case_and_spacing_but_not_wording() -> None:
    assert reply_hash("Good  news\n") == reply_hash("good news")
    assert reply_hash("good news") != reply_hash("good newz")
    assert len(reply_hash("x")) == 32
