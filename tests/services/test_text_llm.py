"""``services._text_llm`` — which provider writes a text, and what its request carries."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from schemas.warming import WarmingSettingsSecret
from services._text_llm import text_llm, uses_deepseek

if TYPE_CHECKING:
    from schemas._warming_settings import TextLlmProvider


def _secret(
    provider: TextLlmProvider,
    *,
    gemini: str = "",
    deepseek: str = "",
) -> WarmingSettingsSecret:
    return WarmingSettingsSecret(
        inter_account_chat=False,
        reactions_enabled=True,
        gemini_api_key=gemini,
        gemini_model="gemini-model",
        gemini_max_retries=3,
        gemini_min_interval_seconds=2.5,
        deepseek_api_key=deepseek,
        text_llm_provider=provider,
        updated_at="2026-09-30T00:00:00+00:00",
    )


@pytest.mark.parametrize(
    ("provider", "gemini", "deepseek", "expected"),
    [
        # Both keys: the choice decides.
        ("deepseek", "g", "d", True),
        ("gemini", "g", "d", False),
        # Only the other one keyed: it stands in.
        ("deepseek", "g", "", False),
        ("gemini", "", "d", True),
        # Only the chosen one keyed: it writes.
        ("deepseek", "", "d", True),
        ("gemini", "g", "", False),
        # No key at all: the choice stands, and the caller reports "no key".
        ("deepseek", "", "", True),
        ("gemini", "", "", False),
    ],
)
def test_the_choice_wins_unless_only_the_other_provider_has_a_key(
    provider: TextLlmProvider,
    gemini: str,
    deepseek: str,
    expected: bool,  # noqa: FBT001 - table input.
) -> None:
    assert uses_deepseek(_secret(provider, gemini=gemini, deepseek=deepseek)) is expected


def test_a_deepseek_request_carries_its_key_model_and_the_operator_pacing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.deepseek, "model", "deepseek-flash")
    request = text_llm(_secret("deepseek", gemini="g", deepseek="d")).request("hi")

    assert (request.api_key, request.model) == ("d", "deepseek-flash")
    assert request.temperature == settings.deepseek.temperature
    assert request.max_output_tokens == settings.deepseek.max_output_tokens
    # Named for Gemini, applied to whichever provider writes.
    assert (request.max_retries, request.min_interval_seconds) == (3, 2.5)


def test_a_gemini_request_uses_the_stored_model_and_passes_the_overrides_through() -> None:
    request = text_llm(_secret("gemini", gemini="g", deepseek="d")).request(
        "hi", max_output_tokens=77, image_b64="aW1n", response_json_object=True
    )

    assert (request.api_key, request.model) == ("g", "gemini-model")
    assert request.max_output_tokens == 77
    assert request.image_b64 == "aW1n"
    assert request.response_json_object is True
    assert (request.max_retries, request.min_interval_seconds) == (3, 2.5)
