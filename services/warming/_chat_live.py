"""The live side of a DM turn: a reaction, a sticker, a forwarded post (Прогрев 2.0, 4.7).

People do not answer every message with text. Sometimes the account reacts to the
partner's message and lets the thread rest; sometimes it answers with a sticker that
fits the mood, picked from its own installed sets; sometimes it opens with a post it
just read («смотри»). Answering hours later needs nothing here: the partner simply
replies on its own next visit.

Every variant runs inside the dialogue turn, after ``_chat._should_chat`` passed. The
fleet DM pause (1.5), ``dm_min_age``, trust and readiness all still apply, and the
fleet pause is read again right before each send. Media only goes to a pair that has
already exchanged ``dm_media_min_exchanges`` messages. No contact is ever saved
(decision Q2): the partner is addressed the way a text DM addresses them.
"""

from __future__ import annotations

from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.db import (
    count_pair_messages_since,
    mark_message_unreplied,
    pair_key,
    recent_pair_messages,
    record_dialogue_message,
    try_claim_message_reply,
)
from core.logging import log_event
from schemas.telegram_actions import SendDirectMessage
from schemas.telegram_actions_warming_social import WarmDmForward, WarmDmReact, WarmDmSticker
from services._account_language import account_language
from services.warming import _seams
from services.warming._character_data import FALLBACK_LANGUAGE, FORWARD_WORDS
from services.warming._dm_breaker import fleet_dm_paused_now
from services.warming.pacing import _HALT_STATUSES

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.dialogues import DialogueMessage
    from schemas.telegram_actions import ActionResult

# The posts this visit read, by channel — what an opener may forward. Set by the cycle
# just before the dialogue turn, so the turn never forwards a post the account has not
# "seen" in this very visit.
_READ_POSTS: ContextVar[dict[str, list[int]] | None] = ContextVar(
    "warming_read_posts", default=None
)


def remember_read_posts(recent_ids: dict[str, list[int]]) -> None:
    """Hand the visit's read posts to the dialogue turn that follows."""
    _READ_POSTS.set({channel: ids for channel, ids in recent_ids.items() if ids})


# What the transcript shows for a forward whose word did not go out.
_FORWARD_MARK = "📎"


class LiveTurn:
    """What a live variant did, in ``_chat.ChatResult`` terms (kept apart to avoid a cycle)."""

    def __init__(
        self,
        *,
        messages_sent: int = 0,
        attempted: int = 1,
        flood: ActionResult | None = None,
        failed_action: str | None = None,
    ) -> None:
        self.messages_sent = messages_sent
        self.attempted = attempted
        self.flood = flood
        self.failed_action = failed_action

    def fields(self) -> dict[str, object]:
        """The matching ``_chat.ChatResult`` keyword arguments."""
        return {
            "messages_sent": self.messages_sent,
            "attempted_actions": self.attempted,
            "flood_result": self.flood,
            "last_failed_action": self.failed_action,
        }


async def _media_unlocked(sender_id: str, partner_id: str) -> bool:
    warm = settings.warming
    since = (
        datetime.now(UTC) - timedelta(hours=warm.dialogue_conversation_window_hours)
    ).isoformat()
    exchanged = await count_pair_messages_since(pair_key(sender_id, partner_id), since)
    return exchanged >= warm.dm_media_min_exchanges


async def _is_newest(sender_id: str, incoming: DialogueMessage) -> bool:
    """Whether ``incoming`` is the pair's newest message.

    Only then is the partner's newest Telegram message — the one ``WarmDmReact`` reacts
    to — this very one, so the claimed row and the reaction cannot drift apart.
    """
    latest = await recent_pair_messages(pair_key(sender_id, incoming.from_account), 1)
    return bool(latest) and latest[-1].id == incoming.id


async def live_reply(
    sender_id: str,
    incoming: DialogueMessage,
    target: AccountRead,
) -> LiveTurn | None:
    """Maybe answer ``incoming`` with a reaction or a sticker; ``None`` = answer in text."""
    if target.user_id is None:
        return None
    warm = settings.warming
    roll = _seams.rng.random()
    if roll < warm.dm_reaction_probability and await _is_newest(sender_id, incoming):
        action: WarmDmReact | WarmDmSticker | None = WarmDmReact(
            user_id=target.user_id,
            peer_phone=target.phone,
            emoji=_seams.rng.choice(warm.dm_reaction_emoji),
        )
    elif (
        warm.dm_reaction_probability
        <= roll
        < warm.dm_reaction_probability + warm.dm_sticker_probability
        and await _media_unlocked(sender_id, incoming.from_account)
    ):
        action = WarmDmSticker(
            user_id=target.user_id,
            peer_phone=target.phone,
            emoticon=_seams.rng.choice(warm.dm_sticker_moods),
            pick=_seams.rng.randint(0, 99),
        )
    else:
        action = None
    if action is None:
        return None
    if await fleet_dm_paused_now() or not await try_claim_message_reply(incoming.id):
        return LiveTurn(attempted=0)
    result = await _seams.execute(sender_id, action)
    if result.status in _HALT_STATUSES:
        await mark_message_unreplied(incoming.id)
        return LiveTurn(flood=result, failed_action=action.action_type)
    if result.status != "ok" or result.warm_skip is not None:
        # Nothing landed (no sticker installed, no message to react to, a refusal):
        # the message waits for the next visit's answer — "read it, replied later".
        await mark_message_unreplied(incoming.id)
        return LiveTurn()
    if isinstance(action, WarmDmSticker):
        # The partner sees the emoticon in the transcript and can answer the sticker.
        await record_dialogue_message(sender_id, incoming.from_account, action.emoticon)
    await log_event(
        "INFO",
        "warming_dialogue_live_reply",
        account_id=sender_id,
        extra={"to": incoming.from_account, "kind": action.action_type},
    )
    return LiveTurn(messages_sent=1 if isinstance(action, WarmDmSticker) else 0)


async def live_opener(sender: AccountRead | None, target: AccountRead) -> LiveTurn | None:
    """Maybe open with a post this visit read; ``None`` = open in text."""
    posts = _READ_POSTS.get()
    if (
        sender is None
        or target.user_id is None
        or not posts
        or _seams.rng.random() >= settings.warming.dm_forward_probability
        or not await _media_unlocked(sender.account_id, target.account_id)
    ):
        return None
    if await fleet_dm_paused_now():
        return LiveTurn(attempted=0)
    channel = _seams.rng.choice(sorted(posts))
    language = account_language(sender.phone) or FALLBACK_LANGUAGE
    comment = FORWARD_WORDS.get(language, FORWARD_WORDS["en"])
    action = WarmDmForward(
        user_id=target.user_id,
        peer_phone=target.phone,
        channel=channel,
        message_id=_seams.rng.choice(posts[channel]),
    )
    result = await _seams.execute(sender.account_id, action)
    if result.status in _HALT_STATUSES:
        return LiveTurn(flood=result, failed_action=action.action_type)
    if result.status != "ok" or result.warm_skip is not None:
        return LiveTurn()
    # The forward landed: the pair has a message whatever the word does next, so it is
    # recorded now and an opener never repeats a delivered forward.
    word = await _seams.execute(
        sender.account_id,
        SendDirectMessage(user_id=target.user_id, text=comment, peer_phone=target.phone),
    )
    said = word.status == "ok"
    await record_dialogue_message(
        sender.account_id, target.account_id, comment if said else _FORWARD_MARK
    )
    await log_event(
        "INFO",
        "warming_dialogue_live_opener",
        account_id=sender.account_id,
        extra={"to": target.account_id, "channel": channel},
    )
    flood = word if word.status in _HALT_STATUSES else None
    return LiveTurn(
        messages_sent=1,
        attempted=2,
        flood=flood,
        failed_action="send_dm" if flood is not None else None,
    )
