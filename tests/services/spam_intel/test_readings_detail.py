"""No word of a reply reaches an evidence ``detail``: only an ISO end date or an enumerated code."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from schemas.spam_status import SpamStatusProbe
from services._spam_parse import (
    AMBIGUOUS_DETAIL,
    UNRECOGNIZED_DETAIL,
    classify_legacy,
    classify_strict,
)
from services.spam_intel._readings import reading_evidence

if TYPE_CHECKING:
    from collections.abc import Callable

    from schemas.spam_status import SpamStatusKind

    Classifier = Callable[[SpamStatusProbe], tuple[SpamStatusKind, str | None]]

AT = datetime(2026, 11, 1, tzinfo=UTC)
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
ALLOWED_CODES = {None, UNRECOGNIZED_DETAIL, AMBIGUOUS_DETAIL}
REPLIES = [
    "Hello Maria Garcia @maria_g, +34 612 345 678: your account is limited until 29 Oct 2026.",
    "Good news, no limits are applied to maria@example.com.",
    "Good news, no limits. Your account is limited because of Maria's spam.",
    "Your account is being checked, Maria.",
    "Hola Maria, tu cuenta está en orden, escribe a @someone.",
    "На ваш аккаунт наложены ограничения до 29 окт. 2026, Мария.",
]


@pytest.mark.parametrize("reply", REPLIES)
@pytest.mark.parametrize("classify", [classify_legacy, classify_strict])
def test_an_evidence_detail_is_a_date_or_a_code_never_the_replys_words(
    reply: str, classify: Classifier
) -> None:
    status, detail = classify(SpamStatusProbe(account_id="a", reply_text=reply))
    evidence = reading_evidence("a", status, detail, AT)
    if evidence is None:
        return
    stored = evidence.detail
    assert stored in ALLOWED_CODES or ISO_DATE.fullmatch(stored or "")
    for word in ("Maria", "Garcia", "maria_g", "345", "example", "someone", "Мария"):
        assert word not in (stored or "")
