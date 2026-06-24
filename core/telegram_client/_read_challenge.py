"""``WaitForBotChallenge`` — match predicate + subscription shell (Ф2 #145).

Split out of ``_read`` to keep that module within the file-size budget (mirrors
``_read_stories``). The match logic (``_extract_bot_challenge``) is pure and unit-
tested against duck-typed messages; the ``events.NewMessage`` + ``asyncio.wait_for``
shell is live I/O. Predicate bias: false-negative > false-positive (a wrong click
can get the account kicked; a missed challenge is recoverable).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from telethon import events
from telethon.tl.types import (
    MessageActionChatAddUser,
    MessageEntityMentionName,
    MessageEntityTextUrl,
    MessageMediaPhoto,
    ReplyInlineMarkup,
)

from schemas.challenge import BotChallengeMessage
from schemas.telegram_actions import BotChallengeWaitResult, WaitForBotChallenge

if TYPE_CHECKING:
    from telethon import TelegramClient


def _inline_button_rows(message: object) -> list[list[str]]:
    """Row-ordered inline-keyboard labels for ``message``, or ``[]`` if none.

    Only ``ReplyInlineMarkup`` counts — a reply-keyboard is not a clickable
    challenge.
    """
    markup = getattr(message, "reply_markup", None)
    if not isinstance(markup, ReplyInlineMarkup):
        return []
    return [
        [str(getattr(button, "text", "")) for button in getattr(row, "buttons", []) or []]
        for row in getattr(markup, "rows", []) or []
    ]


def _reply_is_my_join(replied_action: object | None, my_user_id: int) -> bool:
    """True when the bot's message replies to *our* join service-message."""
    if isinstance(replied_action, MessageActionChatAddUser):
        return my_user_id in (getattr(replied_action, "users", []) or [])
    return False


def _addressed_to_us(
    message: object,
    replied_action: object | None,
    my_user_id: int,
    my_username: str | None,
) -> bool:
    """Only true when the challenge clearly targets *our* account (bias: strict)."""
    text = getattr(message, "message", "") or ""
    if my_username and f"@{my_username}" in text:
        return True
    for entity in getattr(message, "entities", []) or []:
        if isinstance(entity, MessageEntityMentionName) and entity.user_id == my_user_id:
            return True
        if isinstance(entity, MessageEntityTextUrl) and entity.url == f"tg://user?id={my_user_id}":
            return True
    return _reply_is_my_join(replied_action, my_user_id)


def _extract_bot_challenge(
    message: object,
    *,
    replied_action: object | None,
    my_user_id: int,
    my_username: str | None,
) -> BotChallengeMessage | None:
    """Return a typed challenge iff ``message`` is a bot inline-button prompt for us."""
    if not getattr(getattr(message, "sender", None), "bot", False):
        return None
    rows = _inline_button_rows(message)
    if not rows:
        return None
    if not _addressed_to_us(message, replied_action, my_user_id, my_username):
        return None
    return BotChallengeMessage(
        text=getattr(message, "message", "") or "",
        button_labels=[label for row in rows for label in row],
        message_id=int(getattr(message, "id", 0)),
        has_photo=isinstance(getattr(message, "media", None), MessageMediaPhoto),
    )


async def dispatch_wait_for_bot_challenge(
    client: TelegramClient,
    action: WaitForBotChallenge,
) -> BotChallengeWaitResult:  # pragma: no cover - live event-subscription I/O
    """Wait for the first bot inline-button challenge addressed to us, or time out.

    Registers a transient ``NewMessage`` handler on ``chat_id``; the first message
    satisfying ``_extract_bot_challenge`` resolves the wait. ``asyncio.wait_for``
    bounds it; the handler is always removed afterwards.
    """
    me = await client.get_me()
    my_user_id = int(getattr(me, "id", 0))
    my_username = getattr(me, "username", None)
    future: asyncio.Future[BotChallengeMessage] = asyncio.get_running_loop().create_future()

    async def handler(event: events.NewMessage.Event) -> None:
        if future.done():
            return
        message = event.message
        replied_action = None
        if getattr(message, "reply_to", None) is not None:
            replied = await message.get_reply_message()
            replied_action = getattr(replied, "action", None)
        match = _extract_bot_challenge(
            message,
            replied_action=replied_action,
            my_user_id=my_user_id,
            my_username=my_username,
        )
        if match is not None:
            future.set_result(match)

    # Resolve the group entity rather than trusting the bare linked_chat_id (no
    # access_hash); the account just joined on this client, so the cache is warm.
    chat = await client.get_input_entity(action.chat_id)
    client.add_event_handler(handler, events.NewMessage(chats=[chat]))
    try:
        message = await asyncio.wait_for(future, timeout=action.timeout_seconds)
    except TimeoutError:
        return BotChallengeWaitResult(message=None)
    else:
        return BotChallengeWaitResult(message=message)
    finally:
        client.remove_event_handler(handler, events.NewMessage)  # ty: ignore[invalid-argument-type]
