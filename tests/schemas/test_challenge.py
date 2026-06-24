"""Validator tests for ``schemas.challenge.ChallengeDecision`` (Ф2 #146)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas.challenge import ChallengeDecision


def _decision(**overrides: object) -> ChallengeDecision:
    base: dict[str, object] = {"action": "give_up", "confidence": 0.5, "reasoning": "ok"}
    base.update(overrides)
    return ChallengeDecision(**base)  # ty: ignore[invalid-argument-type]


def test_click_button_requires_button_index() -> None:
    with pytest.raises(ValidationError):
        _decision(action="click_button")


def test_click_button_valid_with_index() -> None:
    assert _decision(action="click_button", button_index=0).button_index == 0


def test_send_text_requires_text() -> None:
    with pytest.raises(ValidationError):
        _decision(action="send_text")


def test_send_text_valid_with_text() -> None:
    assert _decision(action="send_text", text="4").text == "4"


def test_give_up_rejects_button_index() -> None:
    with pytest.raises(ValidationError):
        _decision(action="give_up", button_index=1)


def test_give_up_rejects_text() -> None:
    with pytest.raises(ValidationError):
        _decision(action="give_up", text="x")


def test_give_up_valid_with_both_none() -> None:
    decision = _decision(action="give_up")
    assert decision.button_index is None
    assert decision.text is None


@pytest.mark.parametrize("confidence", [1.5, -0.1])
def test_confidence_out_of_range_rejected(confidence: float) -> None:
    with pytest.raises(ValidationError):
        _decision(confidence=confidence)


def test_reasoning_over_200_chars_rejected() -> None:
    with pytest.raises(ValidationError):
        _decision(reasoning="x" * 201)
