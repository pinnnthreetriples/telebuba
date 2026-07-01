"""Gemini-driven inter-account chat — one dialogue turn per cycle.

Reaches Telegram, Gemini and randomness through :mod:`services.warming._seams`
so tests patch those seams in one place.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.db import (
    count_pair_messages_since,
    latest_unreplied_for,
    list_accounts,
    mark_message_replied,
    mark_message_unreplied,
    pair_key,
    record_dialogue_message,
    try_claim_message_reply,
)
from core.logging import log_event
from schemas.gemini import GeminiRequest
from schemas.telegram_actions import ActionResult, SendDirectMessage
from services.content import is_acceptable, release_sent_text, try_reserve_sent
from services.dialogues import get_partners
from services.warming import _seams
from services.warming.pacing import _classify_flood, persona_dm_probability

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.dialogues import DialogueMessage
    from schemas.warming import WarmingCycleRequest, WarmingSettingsSecret
    from services.warming._cycle import _ChannelTally

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


@dataclasses.dataclass
class GenerateResult:
    text: str | None = None
    failure_reason: str | None = None


@dataclasses.dataclass
class ChatResult:
    messages_sent: int = 0
    failures: int = 0
    attempted_actions: int = 0
    flood_result: ActionResult | None = None
    last_failed_action: str | None = None


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
) -> GenerateResult:
    """Generate a chat line, retrying until it passes the filter and dedup.

    ``prompt`` overrides the random opener (used for context-aware replies).
    Returns ``GenerateResult`` with text if successful, or the specific
    failure reason.
    """
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
        if not await try_reserve_sent(candidate):
            await log_event("INFO", "warming_chat_duplicate", account_id=sender_id)
            failure = "chat_duplicate"
            continue
        return GenerateResult(text=candidate)
    return GenerateResult(failure_reason=failure)


async def _maybe_inter_account_chat(
    sender_id: str,
    secret: WarmingSettingsSecret,
) -> ChatResult:
    """Advance one dialogue turn for ``sender_id`` with one of its partners.

    Replies to the most recent unanswered message from a partner; otherwise
    opens a new conversation with an eligible partner. Returns structured result.
    """
    partners = (await get_partners(sender_id)).partners
    if not partners:
        return ChatResult()
    accounts = {account.account_id: account for account in (await list_accounts()).accounts}

    incoming = await latest_unreplied_for(sender_id)
    if incoming is not None and incoming.from_account in partners:
        return await _reply_to_partner(sender_id, incoming, secret, accounts)
    return await _open_with_partner(sender_id, partners, secret, accounts)


async def _run_chat_step(
    data: WarmingCycleRequest,
    secret: WarmingSettingsSecret,
    tally: _ChannelTally,
    *,
    dm_allowed: bool,
    can_attempt: bool,
) -> int:
    """Maybe start/continue an inter-account DM; return messages_sent.

    П11: ``dm_allowed`` is the loop's trust+readiness-aware permission (age-only
    for direct callers). The persona roll is last so it draws only once every
    prior gate passed — it decides *how often* to chat, not whether it may. Any
    flood is folded into ``tally``.
    """
    if not (
        can_attempt
        and not tally.flooded
        and not tally.peer_flooded
        and dm_allowed
        and secret.inter_account_chat
        and secret.gemini_api_key
        and _seams.rng.random() < persona_dm_probability(data.activity_persona)
    ):
        return 0
    chat_result = await _maybe_inter_account_chat(data.account_id, secret)
    tally.attempts += chat_result.attempted_actions
    tally.failures += chat_result.failures
    if chat_result.last_failed_action:
        tally.last_failed_action = chat_result.last_failed_action
    if chat_result.flood_result:
        if chat_result.flood_result.status == "peer_flood":
            tally.peer_flooded = True
        else:
            tally.flooded, tally.flood_seconds, tally.flood_until = _classify_flood(
                chat_result.flood_result,
            )
        tally.last_failed_action = chat_result.last_failed_action or "send_dm"
    return chat_result.messages_sent


async def _reply_to_partner(  # noqa: PLR0911
    sender_id: str,
    incoming: DialogueMessage,
    secret: WarmingSettingsSecret,
    accounts: dict[str, AccountRead],
) -> ChatResult:
    target = accounts.get(incoming.from_account)
    if target is None or target.user_id is None:
        await mark_message_replied(incoming.id)
        return ChatResult()
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
        return ChatResult()
    gen = await _generate_chat_text(
        sender_id,
        secret,
        prompt=_REPLY_PROMPT.format(incoming=incoming.text),
    )
    if gen.text is None:
        return ChatResult(failures=1, last_failed_action=gen.failure_reason)
    text = gen.text
    # Atomic claim before send: collapses ``latest_unreplied_for`` + ``mark``
    # into one UPDATE WHERE replied=0 so two parallel cycles cannot both
    # answer the same incoming message. F6: if the send itself fails (flood
    # or any non-ok), we release the claim so the inbox keeps the message
    # for the next cycle instead of losing it forever.
    if not await try_claim_message_reply(incoming.id):
        # The text reservation in _generate_chat_text would otherwise lock
        # this exact text out of the dedup window for nothing.
        await release_sent_text(text)
        return ChatResult()
    # The text was already reserved by `try_reserve_sent` inside `_generate_chat_text`.
    result = await _seams.execute(sender_id, SendDirectMessage(user_id=target.user_id, text=text))

    if result.status in ("flood_wait", "peer_flood", "slow_mode_wait", "premium_wait"):
        await mark_message_unreplied(incoming.id)
        # P2.6: drop the reservation so the next retry of an identical reply
        # isn't shadowed for the entire dedup window.
        await release_sent_text(text)
        return ChatResult(attempted_actions=1, flood_result=result, last_failed_action="send_dm")
    if result.status != "ok":
        await mark_message_unreplied(incoming.id)
        await release_sent_text(text)
        return ChatResult(failures=1, attempted_actions=1, last_failed_action="send_dm")
    # Chain: record our reply as a new pending message so the partner can answer
    # next cycle — this is what turns a single round-trip into a conversation.
    await record_dialogue_message(sender_id, incoming.from_account, text)
    await log_event(
        "INFO",
        "warming_dialogue_reply",
        account_id=sender_id,
        extra={"to": incoming.from_account},
    )
    return ChatResult(messages_sent=1, attempted_actions=1)


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
) -> ChatResult:
    # F8: when paired loops fire at the same instant, both sides would see an
    # empty inbox and both open the conversation, producing crossing DMs.
    # Restrict the opener role to the lexicographically smaller account_id;
    # the other side waits and replies on its next cycle.
    candidates = [
        accounts[partner]
        for partner in partners
        if accounts.get(partner) is not None
        and accounts[partner].user_id is not None
        and sender_id < partner
    ]
    # Skip partners this pair has already exhausted within the window. The reply
    # path fades (sends nothing) once dialogue_max_turns is hit; an opener that
    # ignored the fade would keep sending fresh one-sided DMs the partner never
    # answers — the spam signature warming avoids. Let the pair rest until the
    # window rolls off, mirroring _reply_to_partner's _conversation_faded gate.
    eligible = [
        account
        for account in candidates
        if not await _conversation_faded(sender_id, account.account_id)
    ]
    if not eligible:
        return ChatResult()
    target = _seams.rng.choice(eligible)
    if target.user_id is None:
        return ChatResult()
    gen = await _generate_chat_text(sender_id, secret)
    if gen.text is None:
        return ChatResult(failures=1, last_failed_action=gen.failure_reason)
    text = gen.text
    # The text was already reserved by `try_reserve_sent` inside `_generate_chat_text`.
    result = await _seams.execute(sender_id, SendDirectMessage(user_id=target.user_id, text=text))

    if result.status in ("flood_wait", "peer_flood", "slow_mode_wait", "premium_wait"):
        # P2.6: drop the reservation so the next opener retry isn't shadowed.
        await release_sent_text(text)
        return ChatResult(attempted_actions=1, flood_result=result, last_failed_action="send_dm")
    if result.status != "ok":
        await release_sent_text(text)
        return ChatResult(failures=1, attempted_actions=1, last_failed_action="send_dm")

    await record_dialogue_message(sender_id, target.account_id, text)
    await log_event(
        "INFO",
        "warming_dialogue_opened",
        account_id=sender_id,
        extra={"to": target.account_id},
    )
    return ChatResult(messages_sent=1, attempted_actions=1)
