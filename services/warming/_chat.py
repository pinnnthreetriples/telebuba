"""Gemini-driven inter-account chat — one dialogue turn per cycle.

Reaches Telegram, Gemini and randomness through :mod:`services.warming._seams`
so tests patch those seams in one place.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.db import (
    count_pair_messages_since,
    latest_unreplied_for,
    list_accounts,
    mark_message_replied,
    pair_key,
    record_dialogue_message,
    try_claim_message_reply,
)
from core.logging import log_event
from schemas.gemini import GeminiRequest
from schemas.telegram_actions import SendDirectMessage
from services.content import is_acceptable, is_duplicate, try_reserve_sent
from services.dialogues import get_partners
from services.warming import _seams

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
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


async def _generate_chat_text(
    sender_id: str,
    secret: WarmingSettingsSecret,
    *,
    prompt: str | None = None,
) -> str | None:
    """Generate a chat line, retrying until it passes the filter and dedup.

    ``prompt`` overrides the random opener (used for context-aware replies).
    Returns ``None`` if generation fails outright or no acceptable, non-duplicate
    text is produced within ``content_max_attempts`` tries.
    """
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
            return None
        candidate = _sanitize_chat_text(generated.text)
        if candidate is None:
            continue
        if not is_acceptable(candidate):
            await log_event("INFO", "warming_chat_filtered", account_id=sender_id)
            continue
        if await is_duplicate(candidate):
            await log_event("INFO", "warming_chat_duplicate", account_id=sender_id)
            continue
        return candidate
    return None


async def _maybe_inter_account_chat(
    sender_id: str,
    secret: WarmingSettingsSecret,
) -> int:
    """Advance one dialogue turn for ``sender_id`` with one of its partners.

    Replies to the most recent unanswered message from a partner; otherwise
    opens a new conversation with an eligible partner. Returns messages sent.
    """
    partners = (await get_partners(sender_id)).partners
    if not partners:
        return 0
    accounts = {account.account_id: account for account in (await list_accounts()).accounts}

    incoming = await latest_unreplied_for(sender_id)
    if incoming is not None and incoming.from_account in partners:
        return await _reply_to_partner(sender_id, incoming, secret, accounts)
    return await _open_with_partner(sender_id, partners, secret, accounts)


async def _reply_to_partner(  # noqa: PLR0911 - each return is a distinct guard (faded/unknown/no-text/claimed/duplicate/send-failed); inlining the early-exits keeps the happy path linear.
    sender_id: str,
    incoming: DialogueMessage,
    secret: WarmingSettingsSecret,
    accounts: dict[str, AccountRead],
) -> int:
    target = accounts.get(incoming.from_account)
    if target is None or target.user_id is None:
        await mark_message_replied(incoming.id)
        return 0
    if await _conversation_faded(sender_id, incoming.from_account):
        # Long enough — let it fade rather than ping-pong forever. Marking the
        # message replied ends the thread; a new one may start after the window.
        await mark_message_replied(incoming.id)
        await log_event(
            "INFO",
            "warming_dialogue_faded",
            account_id=sender_id,
            extra={"with": incoming.from_account},
        )
        return 0
    text = await _generate_chat_text(
        sender_id,
        secret,
        prompt=_REPLY_PROMPT.format(incoming=incoming.text),
    )
    if text is None:
        return 0
    # Atomic claim before send: collapses ``latest_unreplied_for`` + ``mark``
    # into one UPDATE WHERE replied=0 so two parallel cycles cannot both
    # answer the same incoming message. Send-failure after a claim drops the
    # reply by design — silence is safer than a duplicate.
    if not await try_claim_message_reply(incoming.id):
        return 0
    # Same atomic-reserve trick for content dedup — see _open_with_partner.
    if not await try_reserve_sent(text):
        await log_event("INFO", "warming_chat_duplicate", account_id=sender_id)
        return 0
    result = await _seams.execute(sender_id, SendDirectMessage(user_id=target.user_id, text=text))
    if result.status != "ok":
        return 0
    # Chain: record our reply as a new pending message so the partner can answer
    # next cycle — this is what turns a single round-trip into a conversation.
    await record_dialogue_message(sender_id, incoming.from_account, text)
    await log_event(
        "INFO",
        "warming_dialogue_reply",
        account_id=sender_id,
        extra={"to": incoming.from_account},
    )
    return 1


async def _conversation_faded(account_a: str, account_b: str) -> bool:
    """True once a pair has exchanged ``dialogue_max_turns`` within the window."""
    warm = settings.warming
    since = (
        datetime.now(UTC) - timedelta(hours=warm.dialogue_conversation_window_hours)
    ).isoformat()
    count = await count_pair_messages_since(pair_key(account_a, account_b), since)
    return count >= warm.dialogue_max_turns


async def _open_with_partner(
    sender_id: str,
    partners: list[str],
    secret: WarmingSettingsSecret,
    accounts: dict[str, AccountRead],
) -> int:
    candidates = [
        accounts[partner]
        for partner in partners
        if accounts.get(partner) is not None and accounts[partner].user_id is not None
    ]
    if not candidates:
        return 0
    target = _seams.rng.choice(candidates)
    if target.user_id is None:
        return 0
    text = await _generate_chat_text(sender_id, secret)
    if text is None:
        return 0
    # Atomic reserve before send — two parallel cycles cannot both pass the
    # dedup gate for the same text and end up double-sending.
    if not await try_reserve_sent(text):
        await log_event("INFO", "warming_chat_duplicate", account_id=sender_id)
        return 0
    result = await _seams.execute(sender_id, SendDirectMessage(user_id=target.user_id, text=text))
    if result.status != "ok":
        return 0
    await record_dialogue_message(sender_id, target.account_id, text)
    await log_event(
        "INFO",
        "warming_dialogue_opened",
        account_id=sender_id,
        extra={"to": target.account_id},
    )
    return 1
