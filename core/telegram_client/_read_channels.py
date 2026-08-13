"""Read-only channel dispatchers — own-channel list, detail, posts, handle check, liveness.

Extracted-sibling pattern (see ``_read_stories.py``), with one difference: this module owns
its slice of the ``match`` as well as the dispatchers, because ``_read``'s single match hit
the cyclomatic-complexity gate at rank D and the arms that belong with these handlers are
the ones that had somewhere better to be. ``_read`` matches its own arms and falls through
to :func:`_dispatch_channel_read_action` here.

Errors ride the ``execute_read_many`` ladder untouched (RPC → ``TelegramReadError``; the
shared entity guard's ``ChannelGatewayError`` is wrapped there too).
"""

from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING, Literal

from telethon import errors
from telethon.tl.functions.channels import CheckUsernameRequest, GetFullChannelRequest
from telethon.tl.types import ChatReactionsNone, InputChannelEmpty

from core.config import settings
from core.telegram_client._channels import _input_channel
from core.telegram_client._read_discovery import (
    dispatch_get_similar_channels,
    dispatch_search_channels,
)

# Runtime, not ``TYPE_CHECKING``: the dispatcher at the bottom pattern-matches on these
# classes, so they have to exist when it runs and not only when a type checker reads it.
from schemas.telegram_actions import (
    CheckChannelUsername,
    GetLastPostAt,
    GetOwnChannel,
    GetSimilarChannels,
    ListChannelPosts,
    ListOwnChannels,
    SearchChannels,
)
from schemas.telegram_actions_activity import LastPostResult
from schemas.telegram_actions_channels import (
    ChannelUsernameCheck,
    TelegramChannelPost,
    TelegramChannelPosts,
    TelegramOwnChannel,
    TelegramOwnChannelDetail,
    TelegramOwnChannels,
)

if TYPE_CHECKING:
    from pydantic import BaseModel
    from telethon import TelegramClient

    from schemas.telegram_actions import TelegramReadAction


async def dispatch_get_last_post_at(
    client: TelegramClient,
    action: GetLastPostAt,
) -> LastPostResult:
    """The newest message's date, normalised to UTC; ``None`` for an empty channel.

    The one dispatcher here that is NOT about an owned channel: it takes the campaign's
    handle string and lets Telethon resolve it, like every other neurocomment read, rather
    than going through ``_input_channel``.

    ``limit=1`` on purpose: the caller only ever compares this against one cutoff, so a
    page would cost the same RPC and hand it fourteen dates it has no use for. A message
    with no usable date reads as an empty channel rather than an error — Telethon always
    sets one, so the guard is for a stub or a truncated update, and "nothing datable here"
    is what the caller then checks against its own records before acting.
    """
    messages = await client.get_messages(action.channel, limit=1)
    for message in messages:  # ty: ignore[not-iterable]
        date = getattr(message, "date", None)
        if date is not None:
            return LastPostResult(last_post_at=date.astimezone(UTC).isoformat())
    return LastPostResult()


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
    """One owned channel's detail — about/participants from the full chat.

    The id resolves through the shared ``_input_channel`` guard: an unknown /
    unresolvable id raises the stable ``channel_not_found`` code instead of
    letting Telethon's raw ``ValueError`` prose escape the read ladder.

    ``chatFull.chats`` is an unordered vector that also carries the channel's
    linked discussion group, so the requested channel is matched by id (same
    idiom as ``_read._resolve_linked_group_entity``). Index 0 once paired this
    channel's id with the discussion group's title/username — and the edit
    modal prefills from that title.
    """
    entity = await _input_channel(client, action.channel_id)
    full = await client(GetFullChannelRequest(channel=entity))  # ty: ignore[invalid-argument-type]
    full_chat = getattr(full, "full_chat", None)
    chat = next(
        (
            item
            for item in getattr(full, "chats", []) or []
            if int(getattr(item, "id", 0) or 0) == action.channel_id
        ),
        None,
    )
    return TelegramOwnChannelDetail(
        channel_id=action.channel_id,
        title=str(getattr(chat, "title", "") or ""),
        username=getattr(chat, "username", None),
        about=str(getattr(full_chat, "about", "") or ""),
        participants_count=getattr(full_chat, "participants_count", None),
        reactions_enabled=_reactions_enabled(full_chat),
    )


def _reactions_enabled(full_chat: object) -> bool:
    """``chatReactionsNone`` — and an absent field — both mean nobody can react."""
    available = getattr(full_chat, "available_reactions", None)
    return available is not None and not isinstance(available, ChatReactionsNone)


async def dispatch_list_channel_posts(
    client: TelegramClient,
    action: ListChannelPosts,
) -> TelegramChannelPosts:
    """Recent posts newest-first; ``offset_id`` pages strictly below that id.

    Same shared entity guard as the detail read — an unknown id surfaces the
    stable ``channel_not_found`` code, never raw Telethon prose.
    """
    entity = await _input_channel(client, action.channel_id)
    messages = await client.get_messages(
        entity,
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


async def _dispatch_channel_read_action(  # noqa: PLR0911 - one return per read-action case
    client: TelegramClient,
    action: TelegramReadAction,
) -> BaseModel:
    """The own-channel and discovery half of the match above, split off for radon.

    The whole match is one flat type -> dispatcher mapping, so a new read action costs one
    arm and one point of cyclomatic complexity; ``ReadPostComments`` was the arm that took
    the single function to 21 against the D-rank gate's 20. Split by DOMAIN rather than
    down the middle — the arms here are the ones whose handlers live in ``_read_channels``
    and ``_read_discovery``, so both halves keep a coherent subject and both have room for
    the next action.

    Still a ``match`` and not a handler dict: ``ty`` narrows ``action`` to the concrete model
    inside each arm, which is the reason this dispatcher is shaped like this at all, and a
    dict of thunks would hand every handler the union instead.
    """
    match action:
        case ListOwnChannels():
            return await dispatch_list_own_channels(client, action)
        case GetOwnChannel():
            return await dispatch_get_own_channel(client, action)
        case ListChannelPosts():
            return await dispatch_list_channel_posts(client, action)
        case CheckChannelUsername():
            return await dispatch_check_channel_username(client, action)
        case SearchChannels():
            return await dispatch_search_channels(client, action)
        case GetSimilarChannels():
            return await dispatch_get_similar_channels(client, action)
        case GetLastPostAt():
            return await dispatch_get_last_post_at(client, action)
        case _:  # pragma: no cover - discriminated union is exhaustive
            msg = f"Unsupported read action_type: {action.action_type}"
            raise ValueError(msg)
