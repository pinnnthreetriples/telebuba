"""T8 — reading a multi-message transcript: the order of 5.8, ambiguity, the structural read."""

from __future__ import annotations

import pytest

from core.config import settings
from schemas.spam_status import SpamStatusProbe
from services import spam_status
from services._spam_parse import (
    AMBIGUOUS_DETAIL,
    CHECKING_DETAIL,
    UNRECOGNIZED_DETAIL,
    classify_transcript,
)
from tests.services.spam_intel._reply_fixtures import FIXTURES, UNREAD_ES

EN = FIXTURES["en"]
RU = FIXTURES["ru"]
BOTH = "Your account is limited, but good news: no limits apply to your chats."
NOISE = "Tap the button below to learn more."


@pytest.mark.parametrize("strict", [False, True])
def test_any_limited_message_makes_the_transcript_limited_in_any_position(*, strict: bool) -> None:
    for texts in (
        [EN.limited_dated, EN.clean],
        [EN.clean, EN.limited_dated],
        [EN.clean, NOISE, EN.limited_dated],
    ):
        status, detail = classify_transcript(texts, strict=strict)
        assert status == "limited"
        assert detail is not None
        assert "29 Oct 2026" in detail


@pytest.mark.parametrize("strict", [False, True])
def test_the_first_limited_message_gives_the_detail(*, strict: bool) -> None:
    status, detail = classify_transcript(
        [RU.limited_dated, "Your account is restricted until 2026-12-01."], strict=strict
    )
    assert status == "limited"
    assert detail is not None
    assert "29 окт" in detail


def test_limited_is_checked_before_clean_and_before_ambiguity() -> None:
    status, _ = classify_transcript([BOTH, EN.limited_dated], strict=True)
    assert status == "limited"


def test_a_message_matching_both_lexicons_is_ambiguous_not_clean() -> None:
    assert classify_transcript([BOTH], strict=True) == ("unknown", AMBIGUOUS_DETAIL)
    assert classify_transcript([NOISE, BOTH], strict=True) == ("unknown", AMBIGUOUS_DETAIL)


def test_a_clean_message_beside_one_that_says_being_checked_is_ambiguous() -> None:
    texts = [EN.clean, EN.being_checked]
    assert classify_transcript(texts, strict=True) == ("unknown", AMBIGUOUS_DETAIL)
    assert classify_transcript(texts, strict=False) == ("unknown", AMBIGUOUS_DETAIL)


def test_a_clean_message_beside_text_nothing_recognises_stays_clean() -> None:
    """The extra words of a clean reply (a footer, a pointer to the FAQ) do not spoil it."""
    assert classify_transcript([EN.clean, NOISE], strict=True) == ("clean", None)
    assert classify_transcript([NOISE, RU.clean], strict=True) == ("clean", None)


def test_strict_never_says_clean_for_words_it_does_not_know() -> None:
    assert classify_transcript([NOISE, UNREAD_ES], strict=True) == ("unknown", UNRECOGNIZED_DETAIL)


def test_legacy_keeps_its_fallback_for_words_it_does_not_know() -> None:
    assert classify_transcript([NOISE, UNREAD_ES], strict=False) == ("clean", None)


def test_being_checked_alone_is_unknown_with_its_detail() -> None:
    assert classify_transcript([RU.being_checked], strict=True) == ("unknown", CHECKING_DETAIL)
    assert classify_transcript([NOISE, RU.being_checked], strict=True) == (
        "unknown",
        CHECKING_DETAIL,
    )


def test_the_structural_date_read_needs_no_lexicon_in_a_later_message() -> None:
    texts = [UNREAD_ES, "Votre compte reste bloqué jusqu'au 12 juillet 2026."]
    status, detail = classify_transcript(texts, strict=True)
    assert (status, detail) == ("limited", "12 juillet 2026")


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("texts", [[], [""], ["  ", "\n"]])
def test_no_text_at_all_is_unknown_without_a_detail(texts: list[str], *, strict: bool) -> None:
    assert classify_transcript(texts, strict=strict) == ("unknown", None)


def test_several_messages_alone_decide_nothing() -> None:
    """Multi-message and markup are recorded, never read as a verdict, until a sample says so."""
    assert classify_transcript([NOISE, NOISE, NOISE], strict=True) == (
        "unknown",
        UNRECOGNIZED_DETAIL,
    )


@pytest.mark.parametrize("strict", [False, True])
def test_the_executors_classifier_reads_a_transcript_probe(
    monkeypatch: pytest.MonkeyPatch, *, strict: bool
) -> None:
    monkeypatch.setattr(settings.spam_intel, "strict_parser", strict)
    probe = SpamStatusProbe(
        account_id="a",
        reply_text=f"{EN.clean}\n\n{EN.limited_dated}",
        texts=[EN.clean, EN.limited_dated],
        n_messages=2,
        restricted=True,  # never consulted for a transcript
    )
    status, detail = spam_status.classify_spam_probe(probe)
    assert status == "limited"
    assert detail is not None
    assert "29 Oct 2026" in detail


def test_a_single_probe_still_goes_through_the_old_classifiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """n_messages == 0 (every old caller): the restriction flag still counts for the legacy one."""
    monkeypatch.setattr(settings.spam_intel, "strict_parser", False)
    probe = SpamStatusProbe(
        account_id="a", reply_text="hello", restricted=True, restriction_reason="spam"
    )
    assert spam_status.classify_spam_probe(probe) == ("limited", "spam")
