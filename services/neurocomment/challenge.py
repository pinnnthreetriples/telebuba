"""Proactive challenge solver (Ф2 #146).

Called from onboarding right after a successful discussion-group join. Detects a
guardian-bot inline-button challenge, reuses a cached decision or asks Gemini
(server-side ``responseSchema``), humanizes the delay, then clicks / sends the
answer. The audit row is written ``pending``; the engine resolves it to
``solved`` / ``failed`` on the pair's first comment attempt. Image challenges and
any Gemini timeout / parse-fail short-circuit to ``give_up`` (vision deferred).

All Telegram + Gemini + randomness go through ``_seams``; the DB through the repo.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING, Literal

from pydantic import ValidationError

from core.config import settings
from core.db import delete_readiness, insert_challenge, lookup_cached_decision
from schemas.challenge import BotChallengeMessage, ChallengeDecision, ChallengeInsert
from schemas.gemini import GeminiRequest
from schemas.telegram_actions import (
    BotChallengeWaitResult,
    ClickButton,
    PostComment,
    WaitForBotChallenge,
)
from services.content import normalize_text
from services.neurocomment import _seams

if TYPE_CHECKING:
    from schemas.neurocomment import AccountChannelOnboarding

ChallengeOutcome = Literal["no_challenge", "give_up", "solved", "failed"]

# Gemini-compatible (OpenAPI subset) schema for ChallengeDecision — hand-written
# rather than model_json_schema() because responseSchema rejects $defs/anyOf.
_DECISION_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["click_button", "send_text", "give_up"]},
        "button_index": {"type": "integer", "nullable": True},
        "text": {"type": "string", "nullable": True},
        "confidence": {"type": "number"},
        "reasoning": {"type": "string"},
    },
    "required": ["action", "confidence", "reasoning"],
}


def _challenge_hash(text: str, button_labels: list[str]) -> str:
    """Stable global cache key: normalized text joined with sorted button labels."""
    payload = normalize_text(text) + "|" + "|".join(sorted(button_labels))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _build_prompt(message: BotChallengeMessage) -> str:
    buttons = "\n".join(f"{i}: {label}" for i, label in enumerate(message.button_labels))
    return (
        "A guardian bot challenges a new group member to prove they are human. "
        "Pick the single action that passes it.\n\n"
        f"Challenge text:\n{message.text or '(no text)'}\n\n"
        f"Inline buttons (index: label):\n{buttons or '(none)'}\n\n"
        "click_button -> give button_index; send_text -> give text; else give_up. "
        "reasoning <= 200 chars."
    )


async def _gemini_decision(message: BotChallengeMessage) -> ChallengeDecision | None:
    try:
        # GeminiRequest build can raise ValidationError when no API key is
        # configured — treat a misconfigured / timed-out Gemini as give_up rather
        # than crash onboarding (the solver runs before any opt-in flag gate).
        request = GeminiRequest(
            api_key=settings.gemini.api_key,
            prompt=_build_prompt(message),
            model=settings.gemini.model,
            temperature=settings.gemini.temperature,
            max_output_tokens=settings.gemini.max_output_tokens,
            response_schema_json=_DECISION_SCHEMA,
        )
        result = await asyncio.wait_for(
            _seams.generate_text(request),
            timeout=settings.neurocomment.challenge_gemini_timeout_seconds,
        )
    except (TimeoutError, ValidationError):
        return None
    if result.status != "ok" or result.text is None:
        return None
    try:
        decision = ChallengeDecision.model_validate_json(result.text)
    except ValidationError:
        return None
    return _canonicalize_index(decision, message)


def _canonicalize_index(
    decision: ChallengeDecision, message: BotChallengeMessage
) -> ChallengeDecision:
    """Re-base a fresh ``click_button`` index to the sorted-label order (the cache order).

    Gemini's ``button_index`` is positional in the message it saw; persisting/replaying
    it relative to ``sorted(labels)`` makes a cached decision order-robust — a later
    instance sharing the label set clicks the same label regardless of layout order.
    """
    if decision.action != "click_button" or decision.button_index is None:
        return decision
    labels = message.button_labels
    if not 0 <= decision.button_index < len(labels):
        return decision
    canonical = sorted(labels).index(labels[decision.button_index])
    return decision.model_copy(update={"button_index": canonical})


async def _decide(message: BotChallengeMessage) -> ChallengeDecision | None:
    """Cached solved decision for this challenge if any, else a fresh Gemini call."""
    cached = await lookup_cached_decision(_challenge_hash(message.text, message.button_labels))
    if cached is not None:
        return cached
    return await _gemini_decision(message)


def _human_delay_seconds() -> float:
    nc = settings.neurocomment
    lo, hi = sorted((nc.challenge_click_delay_min_seconds, nc.challenge_click_delay_max_seconds))
    if hi <= lo:
        return lo
    # Reuse the warming human-delay shape (bursty log-normal) — a property of human
    # behaviour, not domain-specific; the range is neurocomment's own config.
    warm = settings.warming
    fraction = min(1.0, _seams.rng.lognormvariate(warm.delay_lognorm_mu, warm.delay_lognorm_sigma))
    return min(hi, lo + fraction * (hi - lo))


def _cached_label(decision: ChallengeDecision, button_labels: list[str]) -> str | None:
    """The label a cached ``click_button`` decision selected, keyed off the hash order.

    A cached decision's ``button_index`` is stored relative to ``sorted(labels)`` (the
    same order the cache key uses), so it stays correct across shuffled layouts that
    share the same label *set*. ``None`` when the index is out of range (defensive).
    """
    index = decision.button_index
    ordered = sorted(button_labels)
    if index is None or not 0 <= index < len(ordered):
        return None
    return ordered[index]


async def _dispatch(
    account_id: str,
    group_id: int,
    message: BotChallengeMessage,
    decision: ChallengeDecision,
) -> bool:
    if decision.action == "click_button":
        # Replay by LABEL, not the raw positional index: the cached index points into
        # the sorted-label order, so re-derive the label and let the gateway match it
        # on the current (possibly reordered) layout. Fall back to the index if the
        # label can't be resolved (out-of-range on a mismatched cache).
        label = _cached_label(decision, message.button_labels)
        action: ClickButton | PostComment = ClickButton(
            chat_id=group_id,
            message_id=message.message_id,
            button_index=decision.button_index if label is None else None,
            button_text=label,
        )
    else:  # send_text (give_up is handled before dispatch)
        action = PostComment(chat_id=group_id, text=decision.text or "")
    result = await _seams.execute(account_id, action)
    return result.status == "ok"


async def _record(
    account_id: str,
    channel: str,
    message: BotChallengeMessage,
    *,
    outcome: str,
    decision: ChallengeDecision | None,
) -> None:
    await insert_challenge(
        ChallengeInsert(
            challenge_hash=_challenge_hash(message.text, message.button_labels),
            account_id=account_id,
            channel=channel,
            raw_text=message.text,
            button_labels=message.button_labels,
            outcome=outcome,
            decision_json=decision.model_dump_json() if decision is not None else None,
        ),
    )


async def solve_if_present(account_id: str, channel: str, group_id: int) -> ChallengeOutcome:
    """Detect + solve a guardian-bot challenge on a freshly-joined group.

    Returns the pair's onboarding signal: ``no_challenge`` / ``solved`` →
    comment-able (``solved`` is optimistic — the audit row stays ``pending`` until
    the engine confirms on the first comment), ``give_up`` / ``failed`` →
    ``bot_challenge``.
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
    if message.has_photo:
        # Image captcha: vision deferred (Phase 2) → record + give up, no Gemini.
        await _record(account_id, channel, message, outcome="give_up", decision=None)
        return "give_up"
    decision = await _decide(message)
    if decision is None or decision.action == "give_up":
        await _record(account_id, channel, message, outcome="give_up", decision=decision)
        return "give_up"
    await asyncio.sleep(_human_delay_seconds())
    dispatched = await _dispatch(account_id, group_id, message, decision)
    # ponytail: re-onboarding a pair before its first comment can leave the prior
    # pending row orphaned (the engine resolves only the latest); harmless — pending
    # rows feed neither board status nor cache. Add re-onboard dedup if it ever bites.
    await _record(
        account_id,
        channel,
        message,
        outcome="pending" if dispatched else "failed",
        decision=decision,
    )
    return "solved" if dispatched else "failed"


async def retry_pair(account_id: str, channel: str) -> AccountChannelOnboarding:
    """Operator retry (#148): erase the pair's readiness, then re-onboard it.

    Re-running onboarding re-runs the solver (paying Gemini / a fresh cache hit) —
    useful after a prompt or model tweak. Clearing readiness also drops any
    human-skip so the pair is reconsidered.
    """
    # Lazy import: onboarding imports this module, so a top-level import would cycle.
    from services.neurocomment.onboarding import onboard_account_channel  # noqa: PLC0415

    await delete_readiness(account_id, channel)
    return await onboard_account_channel(account_id, channel)
