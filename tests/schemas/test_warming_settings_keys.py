"""LLM keys typed on the settings page are cleaned — or refused — before they are stored.

A key that reaches an HTTP header with a stray newline or a non-ASCII character makes
httpx raise, and the exception text quotes the whole header, key included, into the
logs. Stripping what a paste drags along and refusing anything else that cannot be a
header value keeps such a key out of the database in the first place.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas.warming import WarmingSettingsUpdate

_KEY_FIELDS = ("gemini_api_key", "openai_api_key", "deepseek_api_key")


@pytest.mark.parametrize("field", _KEY_FIELDS)
def test_surrounding_whitespace_and_newline_are_stripped(field: str) -> None:
    update = WarmingSettingsUpdate.model_validate({field: "  sk-abc123\n"})
    assert getattr(update, field) == "sk-abc123"


@pytest.mark.parametrize("field", _KEY_FIELDS)
@pytest.mark.parametrize(("sent", "stored"), [(None, None), ("", ""), ("  \n", "")])
def test_keep_and_clear_semantics_survive(field: str, sent: str | None, stored: str | None) -> None:
    # ``None`` keeps the stored key; an empty string — or only whitespace — clears it.
    update = WarmingSettingsUpdate.model_validate({field: sent})
    assert getattr(update, field) == stored


@pytest.mark.parametrize("field", _KEY_FIELDS)
@pytest.mark.parametrize("bad", ["sk-a b", "sk-a\nb", "sk-a\tb", "sk-\x00b", "sk-é", "sk-\u200b"])
def test_a_key_that_cannot_be_a_header_value_is_refused(field: str, bad: str) -> None:
    with pytest.raises(ValidationError) as caught:
        WarmingSettingsUpdate.model_validate({field: bad})
    # The refusal names the rule, never the value — the 422 body carries ``msg``.
    assert all(bad not in err["msg"] for err in caught.value.errors())
