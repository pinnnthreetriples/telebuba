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

    ``CheckChatInviteRequest`` answers with a preview (``ChatInvite``, no id at all)
    for an account that has not joined, and with the real chat once it has. The
    preview is not a resolution: nothing in it can be sent to. ``ValueError`` is the
    idiom the rest of the gateway uses for exactly this ("not a member"), so the
    executor classifies it like any other refused resolve.
    """
    invite = await client(CheckChatInviteRequest(hash=invite_hash))
    chat = getattr(invite, "chat", None)
    if chat is None:
        raise ChannelGatewayError(_NOT_FOUND)
    return chat


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


async def dispatch_read_chat_messages(
    client: TelegramClient,
    action: ReadChatMessages,
) -> ReadChatMessagesResult:
    """Re-read messages by id; a ``None`` slot means this account cannot see it.

    Media is reported as a KIND and never as bytes: the callers are a reachability
    check and a chat-context read, and neither wants a download.
    """
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
            continue
        previews.append(
            ChatMessagePreview(
                message_id=message_id,
                text=str(getattr(message, "message", None) or ""),
                media_kind=media_kind(getattr(message, "media", None)),
            ),
        )
    return ReadChatMessagesResult(messages=previews, missing_ids=missing)
