"""Warming toward other people: a post's comments read, and the live side of a DM.

The discussion read uses ``messages.getReplies`` and ``messages.readDiscussion`` on the
channel post itself. The linked group is only resolved to learn whether one exists; it
is never joined. The DM writes address the dialogue partner the way
``SendDirectMessage`` does (``_dm._resolve_dm_peer``). They react to the partner's newest
message, send a sticker from the account's installed sets, or forward a post just read.
Log rows carry ids, counts and the emoticon only — never message text.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telethon import errors
from telethon.tl.functions.messages import (
    GetRepliesRequest,
    GetStickersRequest,
    ReadDiscussionRequest,
    SendReactionRequest,
)
from telethon.tl.types import ReactionEmoji

from core.telegram_client._action_results import _DispatchResult
from core.telegram_client._dm import _resolve_dm_peer
from core.telegram_client._read_comments import _resolve_linked_group_entity

if TYPE_CHECKING:
    from telethon import TelegramClient

    from schemas.telegram_actions_warming_social import (
        WarmDmForward,
        WarmDmReact,
        WarmDmSticker,
        WarmReadDiscussion,
    )

# The partner's newest message is among this many of the dialog's last ones.
_DM_LOOKBACK = 10
# The post moved on between the read and this request — nothing to do this cycle.
_DISCUSSION_SKIPS: dict[type[Exception], str] = {
    errors.MsgIdInvalidError: "post_gone",
    errors.MessageIdInvalidError: "post_gone",
}
_FORWARD_SKIPS: dict[type[Exception], str] = {
    errors.ChatForwardsRestrictedError: "forwards_restricted",
    errors.MessageIdInvalidError: "post_gone",
    errors.MessageIdsEmptyError: "post_gone",
}


async def read_discussion(client: TelegramClient, action: WarmReadDiscussion) -> _DispatchResult:
    """Open a post's comment thread and mark it read; no linked group → skip."""
    if await _resolve_linked_group_entity(client, action.channel) is None:
        return _DispatchResult(log_extra={"warm_skip": "no_discussion"})
    peer = await client.get_input_entity(action.channel)
    try:
        replies = await client(
            GetRepliesRequest(
                peer=peer,
                msg_id=action.message_id,
                offset_id=0,
                offset_date=None,
                add_offset=0,
                limit=action.limit,
                max_id=0,
                min_id=0,
                hash=0,
            ),
        )
    except tuple(_DISCUSSION_SKIPS) as exc:
        return _DispatchResult(log_extra={"warm_skip": _DISCUSSION_SKIPS[type(exc)]})
    messages = list(getattr(replies, "messages", ()) or ())
    newest = max((int(getattr(m, "id", 0)) for m in messages), default=0)
    if newest:
        await client(
            ReadDiscussionRequest(peer=peer, msg_id=action.message_id, read_max_id=newest),
        )
    return _DispatchResult(log_extra={"comments": len(messages)})


async def dm_react(client: TelegramClient, action: WarmDmReact) -> _DispatchResult:
    """React to the partner's newest message; none in the recent history → skip."""
    peer = await _resolve_dm_peer(client, action)
    history = await client.get_messages(peer, limit=_DM_LOOKBACK)
    messages = history if isinstance(history, list) else []
    incoming = next((int(getattr(m, "id", 0)) for m in messages if not getattr(m, "out", True)), 0)
    if not incoming:
        return _DispatchResult(log_extra={"warm_skip": "no_message"})
    await client(
        SendReactionRequest(
            peer=peer,
            msg_id=incoming,
            reaction=[ReactionEmoji(emoticon=action.emoji)],
        ),
    )
    return _DispatchResult(message_id=incoming, log_extra={"emoji": action.emoji})


async def dm_sticker(client: TelegramClient, action: WarmDmSticker) -> _DispatchResult:
    """Send an installed sticker matching the emoticon; none installed → skip."""
    found = await client(GetStickersRequest(emoticon=action.emoticon, hash=0))
    stickers = list(getattr(found, "stickers", ()) or ())
    if not stickers:
        return _DispatchResult(log_extra={"warm_skip": "no_sticker"})
    peer = await _resolve_dm_peer(client, action)
    message = await client.send_file(peer, stickers[action.pick % len(stickers)])
    return _DispatchResult(
        message_id=int(getattr(message, "id", 0)) or None,
        log_extra={"emoticon": action.emoticon},
    )


async def dm_forward(client: TelegramClient, action: WarmDmForward) -> _DispatchResult:
    """Forward a post just read to the partner (its word is a separate send)."""
    peer = await _resolve_dm_peer(client, action)
    try:
        forwarded = await client.forward_messages(peer, action.message_id, from_peer=action.channel)
    except tuple(_FORWARD_SKIPS) as exc:
        return _DispatchResult(log_extra={"warm_skip": _FORWARD_SKIPS[type(exc)]})
    first = forwarded[0] if isinstance(forwarded, list) and forwarded else forwarded
    return _DispatchResult(
        message_id=int(getattr(first, "id", 0)) or None,
        log_extra={"channel": action.channel, "source_id": action.message_id},
    )
