"""What a warming account says — prompts, transcript context, filtering, dedup.

Split from :mod:`services.warming._chat` for the file-size budget; that module
keeps the dialogue turn itself (who talks to whom, sending, bookkeeping) and
calls in here for the line to send. Gemini is reached through
:mod:`services.warming._seams` so tests patch it in one place.
"""

from __future__ import annotations

import dataclasses
import re
from typing import TYPE_CHECKING

from core.config import settings
from core.db import pair_key, recent_pair_messages
from core.logging import log_event
from services._text_llm import text_llm
from services.content import (
    is_acceptable,
    similarity,
    strip_markdown_delimiters,
    try_reserve_sent,
)
from services.warming import _seams
from services.warming._chat_prompt import (
    FRIEND,
    ME,
    Voice,
    account_voice,
    opener_prompt,
    reply_prompt,
)
from services.warming._chat_style import apply_style

if TYPE_CHECKING:
    from schemas.dialogues import DialogueMessage
    from schemas.warming import WarmingSettingsSecret

# Control characters: strip from Gemini output before sending it as a DM.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclasses.dataclass
class GenerateResult:
    text: str | None = None
    failure_reason: str | None = None


async def _build_transcript(sender_id: str, partner_id: str) -> tuple[str, list[str]]:
    """Recent pair transcript from ``sender_id``'s POV, plus the raw message texts.

    Labels each line "Me:" when the sender wrote it, else "Friend:". Returns
    ``("", [])`` when context is disabled or the pair has no history — the caller
    then falls back to the context-free opener/reply prompt. The texts are reused
    as the near-duplicate corpus (Task D) so no extra DB query is issued.
    """
    limit = settings.warming.dialogue_context_messages
    if limit <= 0:
        return "", []
    history = await recent_pair_messages(pair_key(sender_id, partner_id), limit)
    if not history:
        return "", []
    lines = [
        f"{ME if message.from_account == sender_id else FRIEND}: {message.text}"
        for message in history
    ]
    return "\n".join(lines), [message.text for message in history]


def _sanitize_chat_text(raw: str) -> str | None:
    """Strip control chars + markdown, trim, enforce length / line limits.

    ``None`` if nothing survives. The markdown strip runs before the length caps so
    the cap applies to what is actually sent. Gemini is not told to avoid markup
    (no prompt here forbids it, and the hashtag/quote instructions show how well
    that holds), and with ``parse_mode`` disabled a ``**bold**`` reply would reach
    the DM with the asterisks showing — see ``services.content``.
    """
    cleaned = strip_markdown_delimiters(_CONTROL_CHARS_RE.sub("", raw)).strip()
    if not cleaned:
        return None
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    lines = lines[: settings.warming.chat_message_max_lines]
    cleaned = "\n".join(lines)
    if len(cleaned) > settings.warming.chat_message_max_chars:
        cleaned = cleaned[: settings.warming.chat_message_max_chars].rstrip()
    return cleaned or None


async def _reply_text(
    sender_id: str,
    secret: WarmingSettingsSecret,
    incoming: DialogueMessage,
) -> GenerateResult:
    """Answer ``incoming``, continuing the thread with context when there is any."""
    transcript, recent_texts = await _build_transcript(sender_id, incoming.from_account)
    voice = await account_voice(sender_id, secret)
    prompt = reply_prompt(voice, incoming.text, transcript)
    return await _generate_chat_text(sender_id, secret, prompt, voice, recent_texts)


async def _opener_text(
    sender_id: str,
    secret: WarmingSettingsSecret,
    partner_id: str,
) -> GenerateResult:
    """Open with ``partner_id``: resume prior history, else a fresh opener."""
    transcript, recent_texts = await _build_transcript(sender_id, partner_id)
    voice = await account_voice(sender_id, secret)
    prompt = opener_prompt(voice, transcript, _seams.rng)
    return await _generate_chat_text(sender_id, secret, prompt, voice, recent_texts)


async def _generate_chat_text(
    sender_id: str,
    secret: WarmingSettingsSecret,
    prompt: str,
    voice: Voice,
    recent_texts: list[str],
) -> GenerateResult:
    """Generate a chat line, retrying until it passes the filter and dedup.

    The account's manner (``voice.style``) is applied to the sanitised line before the
    filter and the dedup see it, so what is checked is what is sent.
    ``recent_texts`` is this conversation's recent lines: a candidate too similar
    to any of them (Task D near-duplicate gate) is rejected and regenerated.
    Returns ``GenerateResult`` with text if successful, or the specific
    failure reason.
    """
    threshold = settings.warming.dialogue_similarity_max
    failure = "generate_chat_text"
    # The Settings page's text LLM, resolved once so a provider cannot change between
    # regeneration attempts; a chosen one without a key hands over to the other.
    llm = text_llm(secret)
    generate = _seams.generate_text_deepseek if llm.use_deepseek else _seams.generate_text
    for _ in range(settings.warming.content_max_attempts):
        generated = await generate(llm.request(prompt))
        if generated.status != "ok" or not generated.text:
            await log_event(
                "WARNING",
                "warming_chat_generation_failed",
                account_id=sender_id,
                extra={"error": generated.error},
            )
            return GenerateResult(failure_reason="generate_chat_text")
        candidate = _sanitize_chat_text(generated.text)
        if candidate is None:
            continue
        if voice.style is not None:
            candidate = apply_style(candidate, voice.style, voice.language, _seams.rng)
        if not is_acceptable(candidate):
            await log_event("INFO", "warming_chat_filtered", account_id=sender_id)
            failure = "chat_content_filtered"
            continue
        if any(similarity(candidate, prior) >= threshold for prior in recent_texts):
            await log_event("INFO", "warming_chat_too_similar", account_id=sender_id)
            failure = "chat_too_similar"
            continue
        if not await try_reserve_sent(candidate):
            await log_event("INFO", "warming_chat_duplicate", account_id=sender_id)
            failure = "chat_duplicate"
            continue
        return GenerateResult(text=candidate)
    return GenerateResult(failure_reason=failure)
