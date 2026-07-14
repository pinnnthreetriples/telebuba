"""Read-only channel dispatchers — own-channel list, detail, posts, handle check.

Extracted-sibling pattern (see ``_read_stories.py``): ``_read.py`` keeps the
match and imports these dispatchers. Errors ride the ``execute_read_many``
ladder (RPC → ``TelegramReadError``) untouched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from telethon import errors
from telethon.tl.functions.channels import CheckUsernameRequest, GetFullChannelRequest
from telethon.tl.types import InputChannelEmpty, PeerChannel

from core.config import settings
from schemas.telegram_actions_channels import (
    ChannelUsernameCheck,
    TelegramChannelPost,
    TelegramChannelPosts,
    TelegramOwnChannel,
    TelegramOwnChannelDetail,
    TelegramOwnChannels,
)

if TYPE_CHECKING:
    from telethon import TelegramClient

    from schemas.telegram_actions import (
        CheckChannelUsername,
        GetOwnChannel,
        ListChannelPosts,
        ListOwnChannels,
    )


async def dispatch_list_own_channels(
    client: TelegramClient,
    action: ListOwnChannels,
) -> TelegramOwnChannels:
    """Owned broadcast channels = dialog entities with creator+broadcast set.

    There is no creator-only "my channels" TL method that also covers PRIVATE
    channels (``GetAdminedPublicChannelsRequest`` misses them), so we scan the
    dialog list — bounded by ``dialogs_scan_limit`` — and keep the broadcast
    channels this account created, username or not.
    """
    items: list[TelegramOwnChannel] = []
    async for dialog in client.iter_dialogs(limit=settings.channels.dialogs_scan_limit):
        entity = getattr(dialog, "entity", None)
        if not (getattr(entity, "broadcast", False) and getattr(entity, "creator", False)):
            continue
        items.append(
            TelegramOwnChannel(
                channel_id=int(getattr(entity, "id", 0) or 0),
                title=str(getattr(entity, "title", "") or ""),
                username=getattr(entity, "username", None),
                participants_count=getattr(entity, "participants_count", None),
            ),
        )
        if len(items) >= action.limit:
            break
    return TelegramOwnChannels(items=items)


async def dispatch_get_own_channel(
    client: TelegramClient,
    action: GetOwnChannel,
) -> TelegramOwnChannelDetail:
    """One owned channel's detail — about/participants from the full chat."""
    full = await client(GetFullChannelRequest(channel=PeerChannel(action.channel_id)))  # ty: ignore[invalid-argument-type]
    full_chat = getattr(full, "full_chat", None)
    chats = getattr(full, "chats", []) or []
    chat = chats[0] if chats else None
    return TelegramOwnChannelDetail(
        channel_id=action.channel_id,
        title=str(getattr(chat, "title", "") or ""),
        username=getattr(chat, "username", None),
        about=str(getattr(full_chat, "about", "") or ""),
        participants_count=getattr(full_chat, "participants_count", None),
    )


async def dispatch_list_channel_posts(
    client: TelegramClient,
    action: ListChannelPosts,
) -> TelegramChannelPosts:
    """Recent posts newest-first; ``offset_id`` pages strictly below that id."""
    messages = await client.get_messages(
        PeerChannel(action.channel_id),
        limit=action.limit,
        offset_id=action.offset_id,
    )
    items = [
        TelegramChannelPost(
            post_id=int(getattr(message, "id", 0) or 0),
            date_unix=_message_date_unix(message),
            text=str(getattr(message, "message", "") or ""),
            media_kind=_post_media_kind(message),
            views=getattr(message, "views", None),
        )
        for message in messages  # ty: ignore[not-iterable]
        if int(getattr(message, "id", 0) or 0)
    ]
    return TelegramChannelPosts(items=items)


def _post_media_kind(message: object) -> Literal["none", "photo", "video", "other"]:
    """Telethon convenience properties: ``.photo`` / ``.video`` pre-classify media."""
    if getattr(message, "photo", None) is not None:
        return "photo"
    if getattr(message, "video", None) is not None:
        return "video"
    if getattr(message, "media", None) is not None:
        return "other"
    return "none"


def _message_date_unix(message: object) -> int:
    """Coerce Telethon's ``message.date`` (a ``datetime``) into a Unix int."""
    raw = getattr(message, "date", None)
    if isinstance(raw, int):
        return raw
    timestamp = getattr(raw, "timestamp", None)
    if callable(timestamp):
        try:
            return int(timestamp())
        except (TypeError, ValueError):
            return 0
    return 0


async def dispatch_check_channel_username(
    client: TelegramClient,
    action: CheckChannelUsername,
) -> ChannelUsernameCheck:
    """Probe a handle's global availability without touching anything.

    ``UsernameInvalidError`` → the invalid code; ``UsernamePurchaseAvailableError``
    (Fragment-auctioned handle) and a plain ``False`` answer → occupied.
    """
    try:
        available = await client(
            CheckUsernameRequest(channel=InputChannelEmpty(), username=action.username),
        )
    except errors.UsernameInvalidError:
        return ChannelUsernameCheck(available=False, code="channel_username_invalid")
    except errors.UsernamePurchaseAvailableError:
        return ChannelUsernameCheck(available=False, code="channel_username_occupied")
    if not available:
        return ChannelUsernameCheck(available=False, code="channel_username_occupied")
    return ChannelUsernameCheck(available=True)
