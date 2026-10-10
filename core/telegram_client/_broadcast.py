"""Chat-broadcast dispatch: write into groups, forward a post, folder chats, writable groups.

The ``broadcast_*`` writes enter through ``_action_families`` (one table row, no new arm
in ``_dispatch_action``) and the two reads through ``_read_channels``' fallback match.
Classification stays in ``execute``/``execute_read``: everything here raises Telethon's
own errors, or ``ChannelGatewayError`` for an unreachable peer, and lets the ladders map
them.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from telethon import errors, utils
from telethon.tl.functions.chatlists import (
    CheckChatlistInviteRequest,
    JoinChatlistInviteRequest,
)
from telethon.tl.types import Channel, Chat
from telethon.tl.types.chatlists import ChatlistInviteAlready

from core.telegram_client._action_results import _DispatchResult
from core.telegram_client._channels import ChannelGatewayError
from core.telegram_client._io import _named_bytes
from core.telegram_client._read_chat import peer_reference
from core.telegram_client._util import sent_message_id
from schemas.telegram_actions_broadcast import (
    BroadcastForwardPost,
    BroadcastGroup,
    BroadcastJoinChatlist,
    BroadcastSendMessage,
    ChatlistResult,
    CheckChatlist,
    ListWritableGroups,
    WritableGroupsResult,
)

if TYPE_CHECKING:
    from telethon import TelegramClient

    from schemas.telegram_actions import TelegramAction

_NOT_FOUND = "chat_not_found"


async def dispatch_broadcast_action(
    client: TelegramClient,
    action: TelegramAction,
) -> _DispatchResult:
    """Run one ``broadcast_*`` write; the match narrows ``action`` for ty."""
    match action:
        case BroadcastSendMessage():
            return _DispatchResult(message_id=await _send(client, action))
        case BroadcastForwardPost():
            return _DispatchResult(message_id=await _forward(client, action))
        case BroadcastJoinChatlist():
            await _join_chatlist(client, action)
            return _DispatchResult()
        case _:  # pragma: no cover - the family table routes only broadcast_* here
            msg = f"Unsupported broadcast action_type: {action.action_type}"
            raise ValueError(msg)


def broadcast_log_extra(action: TelegramAction) -> dict[str, object]:
    """Ids and flags only — the text and the photo bytes never reach a log row."""
    match action:
        case BroadcastSendMessage():
            return {"chat": action.chat, "has_photo": action.photo is not None}
        case BroadcastForwardPost():
            return {"chat": action.chat, "channel": action.channel, "post_id": action.message_id}
        case BroadcastJoinChatlist():
            return {"slug": action.slug, "peer_id": action.peer_id}
        case _:  # pragma: no cover - the family table routes only broadcast_* here
            return {}


async def _send(client: TelegramClient, action: BroadcastSendMessage) -> int | None:
    peer = peer_reference(action.chat)
    if action.typing_seconds > 0:
        async with client.action(peer, "typing"):  # ty: ignore[invalid-context-manager]
            await asyncio.sleep(action.typing_seconds)
    if action.photo is None:
        message = await client.send_message(peer, action.text)
    else:
        message = await client.send_file(
            peer, _named_bytes(action.photo_name, action.photo), caption=action.text
        )
    return sent_message_id(message)


async def _forward(client: TelegramClient, action: BroadcastForwardPost) -> int | None:
    forwarded = await client.forward_messages(
        peer_reference(action.chat),
        action.message_id,
        from_peer=peer_reference(action.channel),
    )
    first = forwarded[0] if isinstance(forwarded, list) and forwarded else forwarded
    return sent_message_id(first)


async def _join_chatlist(client: TelegramClient, action: BroadcastJoinChatlist) -> None:
    """Join the one folder peer with THIS account's access hash.

    Already inside is reported as ``UserAlreadyParticipantError`` so ``execute`` answers
    ``already_participant`` exactly as it does for a plain join.
    """
    invite = await client(CheckChatlistInviteRequest(slug=action.slug))
    if isinstance(invite, ChatlistInviteAlready) and any(
        utils.get_peer_id(peer, add_mark=False) == action.peer_id
        for peer in invite.already_peers or ()
    ):
        raise errors.UserAlreadyParticipantError(request=None)
    chats = getattr(invite, "chats", None) or ()
    chat = next((c for c in chats if getattr(c, "id", None) == action.peer_id), None)
    if chat is None:
        raise ChannelGatewayError(_NOT_FOUND)
    await client(JoinChatlistInviteRequest(slug=action.slug, peers=[utils.get_input_peer(chat)]))


def _is_group(entity: object) -> bool:
    if isinstance(entity, Channel):
        return bool(getattr(entity, "megagroup", False))
    return isinstance(entity, Chat) and not (
        getattr(entity, "deactivated", False) or getattr(entity, "migrated_to", None)
    )


def _can_write(entity: object) -> bool:
    """Admins write anywhere; everyone else needs both the chat and their own rights open."""
    if getattr(entity, "creator", False) or getattr(entity, "admin_rights", None) is not None:
        return True
    for rights in (
        getattr(entity, "default_banned_rights", None),
        getattr(entity, "banned_rights", None),
    ):
        if getattr(rights, "send_messages", False):
            return False
    return True


def _group(entity: object) -> BroadcastGroup:
    username = getattr(entity, "username", None)
    return BroadcastGroup(
        peer_id=int(getattr(entity, "id", 0) or 0),
        title=str(getattr(entity, "title", "") or ""),
        username=username if isinstance(username, str) and username else None,
    )


async def dispatch_list_writable_groups(
    client: TelegramClient,
    action: ListWritableGroups,
) -> WritableGroupsResult:
    """Scan the dialog list once: groups we may write to, channels and admin-only counted."""
    groups: list[BroadcastGroup] = []
    channels = admin_only = 0
    async for dialog in client.iter_dialogs(limit=action.limit):
        entity = getattr(dialog, "entity", None)
        if isinstance(entity, Channel) and not _is_group(entity):
            channels += 1
        elif not _is_group(entity) or int(getattr(entity, "id", 0) or 0) <= 0:
            continue
        elif _can_write(entity):
            groups.append(_group(entity))
        else:
            admin_only += 1
    return WritableGroupsResult(
        groups=groups, channels_skipped=channels, admin_only_skipped=admin_only
    )


async def dispatch_check_chatlist(client: TelegramClient, action: CheckChatlist) -> ChatlistResult:
    """The groups behind a folder link; broadcast channels in it are counted, not listed."""
    invite = await client(CheckChatlistInviteRequest(slug=action.slug))
    title = getattr(invite, "title", "") or ""
    groups: list[BroadcastGroup] = []
    channels = 0
    for chat in getattr(invite, "chats", None) or ():
        if _is_group(chat) and int(getattr(chat, "id", 0) or 0) > 0:
            groups.append(_group(chat))
        elif isinstance(chat, Channel):
            channels += 1
    return ChatlistResult(
        title=str(getattr(title, "text", title)), groups=groups, channels_skipped=channels
    )
