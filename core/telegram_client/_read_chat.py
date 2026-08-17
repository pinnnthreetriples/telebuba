"""Chat-scoped reads: what a target IS to this account, and what a message carries.

Extracted-sibling pattern (see ``_read_stories``): ``_read`` keeps the ``match``
arms and delegates the bodies here.

Both dispatchers exist because a chat id is per-ACCOUNT state. Telethon answers an
unmarked positive int out of the session entity cache, and that cache is a separate
SQLite file per account, so the id account A resolved means nothing to account B —
B raises ``ValueError: Could not find the input entity``. A caller that wants N
accounts talking in one chat therefore resolves it N times, once each, after each of
them has actually joined.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from telethon.tl.functions.messages import CheckChatInviteRequest
from telethon.tl.types import (
    Channel,
    Chat,
    ChatInviteAlready,
    MessageMediaDocument,
    MessageMediaPhoto,
    MessageMediaWebPage,
    User,
)

from core.channel_tokens import extract_invite_hash
from core.telegram_client._channels import ChannelGatewayError
from schemas.telegram_action_results import (
    ChatMessagePreview,
    ReadChatMessagesResult,
    ResolveChatResult,
)

if TYPE_CHECKING:
    from telethon import TelegramClient

    from schemas.telegram_actions_chat import ChatKind, ChatMediaKind, ReadChatMessages, ResolveChat

# Telethon answers an unresolvable peer with a bare ``ValueError``, which is NOT in
# ``execute_read_many``'s ladder and would escape the gateway raw — the exact hole
# ``ChannelGatewayError`` was introduced to close for the channel reads. One stable
# code covers every shape of "this account cannot reach that chat": a username that
# does not exist, an invite it has not accepted, and an id its session never cached.
_NOT_FOUND = "chat_not_found"


def peer_reference(chat: str) -> str | int:
    """A ``chat`` field as Telethon should read it: an id if it is all digits, else a name.

    The digit branch is the raw positive id this package pins everywhere (see
    ``schemas.telegram_actions_chat``); Telethon looks one up in the session entity
    cache across users, chats and channels without an RPC. Passing the same value as a
    STRING would instead send it down the username resolver and fail.
    """
    return int(chat) if chat.isdigit() else chat


def media_kind(media: object) -> ChatMediaKind:
    """Classify a message's media by its CONCRETE class, never by ``is not None``.

    The distinction is load-bearing for the copy path: ``MessageMediaWebPage`` makes
    ``send_file`` raise ``TypeError``, while ``MessageMediaEmpty`` and
    ``MessageMediaUnsupported`` are accepted and send a message with NO media at all —
    a silent wrong result, which is worse than a refusal. Anything not on the
    two-member allow-list is therefore reported as unusable rather than assumed fine.
    """
    if media is None:
        return "none"
    if isinstance(media, MessageMediaPhoto):
        return "photo"
    if isinstance(media, MessageMediaDocument):
        return "document"
    if isinstance(media, MessageMediaWebPage):
        return "web_page"
    return "unsupported"


def _entity_kind(entity: object) -> ChatKind:
    """Which of Telegram's four peer shapes this is.

    ``Channel`` covers both broadcasts and supergroups and is told apart by its
    ``megagroup`` flag; ``Chat`` is the legacy basic group. The split matters because
    basic groups and private chats number their messages PER USER, so ids do not
    agree between two of our accounts.
    """
    if isinstance(entity, Channel):
        return "megagroup" if getattr(entity, "megagroup", False) else "channel"
    if isinstance(entity, Chat):
        return "basic_group"
    if isinstance(entity, User):
        return "user"
    raise ChannelGatewayError(_NOT_FOUND)


async def _resolve_invite(client: TelegramClient, invite_hash: str) -> object:
    """The chat behind a ``+HASH`` invite — only if this account is already inside.

    ``CheckChatInviteRequest`` has three answers and TWO of them carry a chat.
    ``ChatInviteAlready`` is the one that means membership. ``ChatInvite`` is the
    preview an outsider gets and holds no chat at all. ``ChatInvitePeek`` holds the
    real chat and is handed to an account that has NOT joined — a read-only look that
    expires — so accepting whichever answer has a ``chat`` attribute resolved a
    perfectly valid id for a chat the account is not in: nothing recorded its absence,
    the target counted as usable, and every send into it failed afterwards. The class
    is therefore matched rather than the attribute, which is what Telethon's own
    ``get_entity`` does with the same three answers.

    The refusal is ``ChannelGatewayError(_NOT_FOUND)``, like every other unreachable
    peer in this module; ``execute_read`` re-raises it as ``TelegramReadError`` and
    ``services.neuroshilling._telegram.resolve_target`` writes the pair off.
    """
    invite = await client(CheckChatInviteRequest(hash=invite_hash))
    if not isinstance(invite, ChatInviteAlready):
        raise ChannelGatewayError(_NOT_FOUND)
    return invite.chat


async def dispatch_resolve_chat(
    client: TelegramClient,
    action: ResolveChat,
) -> ResolveChatResult:
    """Turn a normalised target token into this account's own chat id and kind."""
    # ``normalize_channel`` hands private targets over as ``+HASH``, which
    # ``extract_invite_hash`` accepts directly — no second spelling needed here.
    invite_hash = extract_invite_hash(action.target)
    if invite_hash:
        entity = await _resolve_invite(client, invite_hash)
    else:
        try:
            entity = await client.get_entity(action.target)
        except ValueError as exc:
            raise ChannelGatewayError(_NOT_FOUND) from exc
    chat_id = int(getattr(entity, "id", 0))
    if chat_id <= 0:
        raise ChannelGatewayError(_NOT_FOUND)
    return ResolveChatResult(chat_id=chat_id, kind=_entity_kind(entity))


def _preview(message: object, message_id: int) -> ChatMessagePreview:
    """One Telethon message flattened to the contract. Media is a KIND, never bytes.

    ``sender_id`` is read through ``int()`` rather than passed through: an anonymous
    admin post carries none, and a test double answers every attribute with another
    mock, so anything that is not already a number is reported as unknown.
    """
    sender = getattr(message, "sender_id", None)
    return ChatMessagePreview(
        message_id=message_id,
        text=str(getattr(message, "message", None) or ""),
        media_kind=media_kind(getattr(message, "media", None)),
        sender_id=sender if isinstance(sender, int) else None,
        outgoing=getattr(message, "out", False) is True,
    )


async def _read_by_ids(
    client: TelegramClient,
    action: ReadChatMessages,
) -> ReadChatMessagesResult:
    """Re-read named messages; a ``None`` slot means this account cannot see it."""
    # get_messages(ids=[...]) returns a list aligned to ids (None where a message is
    # gone or invisible); the stub union also admits the single-id Message form.
    try:
        messages = cast(
            "list[object | None]",
            await client.get_messages(peer_reference(action.chat), ids=action.message_ids),
        )
    except ValueError as exc:
        raise ChannelGatewayError(_NOT_FOUND) from exc
    previews: list[ChatMessagePreview] = []
    missing: list[int] = []
    for message_id, message in zip(action.message_ids, messages, strict=True):
        if message is None:
            missing.append(message_id)
        else:
            previews.append(_preview(message, message_id))
    return ReadChatMessagesResult(messages=previews, missing_ids=missing)


async def _read_since(
    client: TelegramClient,
    action: ReadChatMessages,
) -> ReadChatMessagesResult:
    """The newest ``limit`` messages above the cursor, handed back oldest-first.

    ``get_messages(limit=...)`` walks BACKWARDS from the head of the chat, which is
    what makes ``min_id=0`` mean "the newest ``limit`` messages" instead of "the
    oldest ones". A forward walk would start a first poll at the beginning of the
    chat's history and grind through it a page per poll, replying to years-old
    messages on the way.

    Sorted ascending before returning, because a cursor is only advanced safely by
    the LAST element and the caller reads the conversation in order.
    """
    try:
        messages = cast(
            "list[object]",
            await client.get_messages(
                peer_reference(action.chat),
                limit=action.limit,
                min_id=action.min_id,
            ),
        )
    except ValueError as exc:
        raise ChannelGatewayError(_NOT_FOUND) from exc
    previews = [
        _preview(message, message_id)
        for message in messages
        if message is not None and (message_id := int(getattr(message, "id", 0) or 0)) > 0
    ]
    previews.sort(key=lambda preview: preview.message_id)
    return ReadChatMessagesResult(messages=previews)


async def dispatch_read_chat_messages(
    client: TelegramClient,
    action: ReadChatMessages,
) -> ReadChatMessagesResult:
    """Read a chat either by named ids or from a cursor — the action carries which."""
    if action.min_id is None:
        return await _read_by_ids(client, action)
    return await _read_since(client, action)
