"""A contradictory reply is never clean (F-013); ``restricted`` moves no reading (F-004)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import get_spam_status
from core.repositories.spam_evidence import list_evidence
from schemas.spam_status import SpamStatusProbe
from services import spam_intel, spam_status
from services._spam_parse import (
    AMBIGUOUS_DETAIL,
    classify_legacy,
    classify_strict,
    classify_transcript,
)
from tests.services.spam_intel._reply_fixtures import FIXTURES

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from tests.services.spam_intel.conftest import FakeProbe

    MakeAccount = Callable[..., Awaitable[None]]

EN = FIXTURES["en"]
RU = FIXTURES["ru"]
BOTH_EN = "Good news, no limits are applied. Your account is limited until 29 Oct 2026."
BOTH_RU = "Хорошие новости, не применены никакие ограничения. На аккаунт наложены ограничения."
UNMATCHED = "Hola, tu cuenta está en orden."


def _probe(text: str, **fields: object) -> SpamStatusProbe:
    return SpamStatusProbe.model_validate({"account_id": "a", "reply_text": text, **fields})


@pytest.mark.parametrize("text", [BOTH_EN, BOTH_RU])
def test_one_text_with_both_markers_is_ambiguous_in_both_classifiers(text: str) -> None:
    expected = ("unknown", AMBIGUOUS_DETAIL)
    assert classify_legacy(_probe(text)) == expected
    assert classify_strict(_probe(text)) == expected


@pytest.mark.parametrize("strict", [False, True])
def test_a_transcript_with_a_contradictory_message_is_not_clean(*, strict: bool) -> None:
    expected = ("unknown", AMBIGUOUS_DETAIL)
    assert classify_transcript([BOTH_EN], strict=strict) == expected
    assert classify_transcript([EN.clean, BOTH_EN], strict=strict) == expected
    assert classify_transcript([BOTH_RU, RU.clean], strict=strict) == expected


@pytest.mark.parametrize("strict", [False, True])
def test_a_limited_message_still_decides_the_transcript(*, strict: bool) -> None:
    status, _ = classify_transcript([BOTH_EN, EN.limited_dated], strict=strict)
    assert status == "limited"


def test_the_legacy_fallback_for_a_text_nothing_matched_stays_clean() -> None:
    """The documented limitation until real samples exist; never for a text that matches limited."""
    assert classify_legacy(_probe(UNMATCHED)) == ("clean", None)
    assert classify_strict(_probe(UNMATCHED)) == ("unknown", "unrecognized_reply")


@pytest.mark.asyncio
async def test_a_contradictory_reply_is_stored_as_unknown_with_strict_off(
    make_account: MakeAccount, probe: FakeProbe
) -> None:
    await make_account("acc-1")
    probe.reply = BOTH_EN
    assert settings.spam_intel.strict_parser is False
    verdict = await spam_intel.ask("acc-1", force=True)
    assert (verdict.status, verdict.detail) == ("unknown", AMBIGUOUS_DETAIL)
    stored = await get_spam_status("acc-1")
    assert stored is not None
    assert stored.status == "unknown"


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("text", [UNMATCHED, "something unrelated", EN.clean, EN.being_checked])
def test_the_restricted_flag_changes_no_reading(text: str, *, strict: bool) -> None:
    classify = classify_strict if strict else classify_legacy
    plain = classify(_probe(text))
    flagged = classify(_probe(text, restricted=True, restriction_reason="terms violation"))
    assert flagged == plain


@pytest.mark.asyncio
async def test_a_restricted_account_with_an_unrecognised_reply_is_not_stored_as_limited(
    make_account: MakeAccount, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _restricted(account_id: str) -> SpamStatusProbe:
        return SpamStatusProbe(
            account_id=account_id,
            reply_text="Reply nothing knows about.",
            restricted=True,
            restriction_reason="porn-all: terms",
        )

    monkeypatch.setattr(spam_status, "check_spam_status", _restricted)
    monkeypatch.setattr(settings.spam_intel, "strict_parser", True)
    await make_account("acc-1")
    verdict = await spam_intel.ask("acc-1", force=True)
    assert verdict.status != "limited"
    assert "terms" not in repr(await get_spam_status("acc-1"))
    kinds = {item.kind for item in await list_evidence("acc-1")}
    assert "spambot_limited" not in kinds


@pytest.mark.asyncio
async def test_a_restricted_account_is_not_limited_with_strict_off_either(
    make_account: MakeAccount, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _restricted(account_id: str) -> SpamStatusProbe:
        return SpamStatusProbe(
            account_id=account_id, reply_text="hello", restricted=True, restriction_reason="terms"
        )

    monkeypatch.setattr(spam_status, "check_spam_status", _restricted)
    await make_account("acc-1")
    verdict = await spam_intel.ask("acc-1", force=True)
    assert verdict.status != "limited"
    kinds = {item.kind for item in await list_evidence("acc-1")}
    assert "spambot_limited" not in kinds
