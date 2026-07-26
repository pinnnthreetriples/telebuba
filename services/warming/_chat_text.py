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
from schemas.gemini import GeminiRequest
from services.content import is_acceptable, similarity, try_reserve_sent
from services.warming import _seams

if TYPE_CHECKING:
    from schemas.dialogues import DialogueMessage
    from schemas.warming import WarmingSettingsSecret

# Control characters: strip from Gemini output before sending it as a DM.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_CHAT_PROMPTS = (
    "Напиши одно короткое дружелюбное сообщение для чата в Telegram (1-2 предложения), "
    "без хэштегов и без кавычек.",
    "Сгенерируй одну живую неформальную реплику для переписки в Telegram, "
    "максимум два предложения, без эмодзи-спама.",
    "Придумай короткое сообщение, как будто пишешь приятелю в Telegram. "
    "Только текст, без пояснений.",
)

_REPLY_PROMPT = (
    "Ответь коротко и по-дружески, как другу в Telegram, на это сообщение: "
    "«{incoming}». Только текст ответа, без кавычек."
)

# History-aware prompt: given the recent transcript («Я» = this account) the
# model continues THIS conversation instead of emitting a generic greeting. Only
# the instruction line differs — replying to the last message vs. reopening.
_HISTORY_PROMPT = (
    "Ты и твой друг переписываетесь в Telegram. Вот последние сообщения "
    "(«Я» — это ты):\n{transcript}\n\n{instruction}"
)
_REPLY_INSTRUCTION = (
    "Продолжи разговор естественно: коротко ответь по-дружески на последнюю "
    "реплику собеседника. 1-2 предложения, без кавычек, без хэштегов, без "
    "эмодзи-спама. Только текст ответа."
)
_OPENER_INSTRUCTION = (
    "Напиши короткое сообщение, чтобы естественно возобновить разговор, учитывая "
    "то, что вы обсуждали ранее. 1-2 предложения, без кавычек, без хэштегов. "
    "Только текст."
)


@dataclasses.dataclass
class GenerateResult:
    text: str | None = None
    failure_reason: str | None = None


async def _build_transcript(sender_id: str, partner_id: str) -> tuple[str, list[str]]:
    """Recent pair transcript from ``sender_id``'s POV, plus the raw message texts.

    Labels each line "Я:" when the sender wrote it, else "Собеседник:". Returns
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
        f"{'Я' if message.from_account == sender_id else 'Собеседник'}: {message.text}"
        for message in history
    ]
    return "\n".join(lines), [message.text for message in history]


def _sanitize_chat_text(raw: str) -> str | None:
    """Strip control chars, trim, enforce length / line limits. ``None`` if empty."""
    cleaned = _CONTROL_CHARS_RE.sub("", raw).strip()
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
    prompt = (
        _HISTORY_PROMPT.format(transcript=transcript, instruction=_REPLY_INSTRUCTION)
        if transcript
        else _REPLY_PROMPT.format(incoming=incoming.text)
    )
    return await _generate_chat_text(sender_id, secret, prompt=prompt, recent_texts=recent_texts)


async def _opener_text(
    sender_id: str,
    secret: WarmingSettingsSecret,
    partner_id: str,
) -> GenerateResult:
    """Open with ``partner_id``: resume prior history, else a random cold opener."""
    transcript, recent_texts = await _build_transcript(sender_id, partner_id)
    prompt = (
        _HISTORY_PROMPT.format(transcript=transcript, instruction=_OPENER_INSTRUCTION)
        if transcript
        else None
    )
    return await _generate_chat_text(sender_id, secret, prompt=prompt, recent_texts=recent_texts)


async def _generate_chat_text(
    sender_id: str,
    secret: WarmingSettingsSecret,
    *,
    prompt: str | None = None,
    recent_texts: list[str] | None = None,
) -> GenerateResult:
    """Generate a chat line, retrying until it passes the filter and dedup.

    ``prompt`` overrides the random opener (used for context-aware replies).
    ``recent_texts`` is this conversation's recent lines: a candidate too similar
    to any of them (Task D near-duplicate gate) is rejected and regenerated.
    Returns ``GenerateResult`` with text if successful, or the specific
    failure reason.
    """
    recent_texts = recent_texts or []
    threshold = settings.warming.dialogue_similarity_max
    failure = "generate_chat_text"
    for _ in range(settings.warming.content_max_attempts):
        generated = await _seams.generate_text(
            GeminiRequest(
                api_key=secret.gemini_api_key,
                prompt=prompt or _seams.rng.choice(_CHAT_PROMPTS),
                model=secret.gemini_model,
                temperature=settings.gemini.temperature,
                max_output_tokens=settings.gemini.max_output_tokens,
            ),
        )
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
