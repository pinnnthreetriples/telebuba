"""Proactive challenge solver — detection-only stub (Ф2 #145).

Called from onboarding right after a successful discussion-group join. This slice
*detects* a guardian-bot inline-button challenge and records it for the operator,
but never calls Gemini — it always gives up. The Gemini decision, cache lookup,
and humanized click land in #146.

All Telegram access goes through ``_seams``; the audit row is persisted via the
core repository. The challenge hash (cache key for #146) is computed here.
"""

from __future__ import annotations

import hashlib
from typing import Literal

from core.config import settings
from core.db import insert_challenge
from schemas.challenge import ChallengeInsert
from schemas.telegram_actions import BotChallengeWaitResult, WaitForBotChallenge
from services.content import normalize_text
from services.neurocomment import _seams

ChallengeOutcome = Literal["no_challenge", "give_up"]


def _challenge_hash(text: str, button_labels: list[str]) -> str:
    """Stable global cache key: normalized text joined with sorted button labels."""
    payload = normalize_text(text) + "|" + "|".join(sorted(button_labels))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def solve_if_present(account_id: str, channel: str, group_id: int) -> ChallengeOutcome:
    """Detect a guardian-bot challenge in the just-joined group; record it, give up.

    Returns ``"no_challenge"`` when nothing arrives within the wait window (the pair
    is comment-able), or ``"give_up"`` when a challenge is detected — an audit row is
    written (no Gemini in this slice) and the board renders the channel ``bot_challenge``.
    """
    result = await _seams.execute_read(
        account_id,
        WaitForBotChallenge(
            chat_id=group_id,
            timeout_seconds=settings.neurocomment.challenge_wait_timeout_seconds,
        ),
    )
    message = result.message if isinstance(result, BotChallengeWaitResult) else None
    if message is None:
        return "no_challenge"
    await insert_challenge(
        ChallengeInsert(
            challenge_hash=_challenge_hash(message.text, message.button_labels),
            account_id=account_id,
            channel=channel,
            raw_text=message.text,
            button_labels=message.button_labels,
            outcome="give_up",
        ),
    )
    return "give_up"
