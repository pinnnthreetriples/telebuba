"""Semantic contracts for guardian-challenge prompts."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

from schemas.challenge import BotChallengeMessage
from services.neurocomment import challenge

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.mark.parametrize("builder", [challenge._build_prompt, challenge._build_vision_prompt])
def test_challenge_prompt_preserves_evidence_and_action_mapping(
    builder: Callable[[BotChallengeMessage], str],
) -> None:
    """Provider input keeps raw evidence and the decision schema's action vocabulary."""
    message = BotChallengeMessage(
        text="Type MiXeD-42 exactly; do not normalize me.",
        button_labels=["Keep Case", "Отмена!"],
        message_id=17,
    )

    prompt = builder(message)

    assert message.text in prompt
    for index, label in enumerate(message.button_labels):
        assert f"{index}: {label}" in prompt
    action_vocabulary = set(re.findall(r"[a-z_]+", prompt.casefold()))
    assert {"click_button", "send_text", "give_up"} <= action_vocabulary
