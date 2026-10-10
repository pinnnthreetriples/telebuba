"""User-parser reads: a chat's members, a chat's message authors, a post's commenters.

Extracted-sibling pattern (see ``_read_chat``): ``_read`` keeps one or-patterned arm for
the family and :func:`dispatch_user_parser_read` routes inside it.

Every user is flattened to :class:`ParsedTelegramUser` here, so nothing above ``core``
sees a Telethon ``User`` — and nothing reaches the contract that the parser must not keep
(phone, ``access_hash``). A chat reached by an invite is read by the positive id
``ResolveChat`` handed back, which Telethon finds in the session's own entity cache.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

from telethon.tl.functions.channels import GetFullChannelRequest, GetParticipantsRequest
from telethon.tl.functions.messages import GetFullChatRequest
from telethon.tl.types import (
    Channel,
    ChannelParticipantsAdmins,
    ChannelParticipantsSearch,
    Chat,
    ChatParticipantAdmin,
    ChatParticipantCreator,
    User,
    UserStatusLastMonth,
    UserStatusLastWeek,
    UserStatusOffline,
    UserStatusOnline,
    UserStatusRecently,
)

from core.telegram_client._channels import ChannelGatewayError
from core.telegram_client._read_chat import peer_reference
from schemas.telegram_actions_user_parser import (
    ChatHistoryMessage,
    LastSeenBucket,
    ParsedTelegramUser,
    ReadChannelPostReplies,
    ReadChannelPostRepliesResult,
    ReadChatHistoryAuthors,
    ReadChatHistoryAuthorsResult,
    ReadChatParticipants,
    ReadChatParticipantsResult,
)

if TYPE_CHECKING:
    from pydantic import BaseModel
    from telethon import TelegramClient

_NOT_FOUND = "chat_not_found"
# An exact "last seen" older than this many days is a month bucket, then "long ago" —
# the same cut-offs Telegram uses for the approximate statuses.
_RECENT_DAYS = 3
_WEEK_DAYS = 7
_MONTH_DAYS = 30


_BUCKETS: dict[type, LastSeenBucket] = {
    UserStatusOnline: "online",
    UserStatusRecently: "recently",
    UserStatusLastWeek: "week",
    UserStatusLastMonth: "month",
}


def _offline_bucket(was: object, now: datetime) -> LastSeenBucket:
    """An exact "last seen" binned with the cut-offs of the approximate statuses."""
    if not isinstance(was, datetime):
        return "long"
    age = now - (was if was.tzinfo else was.replace(tzinfo=UTC))
    for days, bucket in ((_RECENT_DAYS, "recently"), (_WEEK_DAYS, "week"), (_MONTH_DAYS, "month")):
        if age <= timedelta(days=days):
            return cast("LastSeenBucket", bucket)
    return "long"


def _last_seen(status: object, now: datetime) -> LastSeenBucket:
    """Telegram's status object as one bucket. An exact time is binned like the rest."""
    bucket = _BUCKETS.get(type(status))
    if bucket is not None:
        return bucket
    if isinstance(status, UserStatusOffline):
        return _offline_bucket(getattr(status, "was_online", None), now)
    # ``UserStatusEmpty`` is "a long time ago"; no status at all is nothing to bin.
    return "hidden" if status is None else "long"


def _username(user: User) -> str | None:
    """The public handle, or the first active collectible one when the main is unset."""
    if user.username:
        return user.username
    for extra in user.usernames or ():
        if getattr(extra, "active", False) and getattr(extra, "username", None):
            return str(extra.username)
    return None


def parsed_user(user: User, now: datetime) -> ParsedTelegramUser:
    """One Telethon ``User`` as the parser's record — public profile facts only."""
    return ParsedTelegramUser(
        user_id=int(user.id),
        username=_username(user),
        first_name=user.first_name or "",
        last_name=user.last_name or "",
        is_bot=bool(user.bot),
        is_deleted=bool(user.deleted),
        is_scam=bool(user.scam),
        is_fake=bool(user.fake),
        is_premium=bool(user.premium),
        has_photo=user.photo is not None,
        has_stories=bool(user.stories_max_id) and not user.stories_unavailable,
        last_seen=_last_seen(user.status, now),
    )


def _users(entities: object, now: datetime) -> list[ParsedTelegramUser]:
    return [
        parsed_user(entity, now)
        for entity in cast("list[object]", entities or [])
        if isinstance(entity, User)
    ]


async def _entity(client: TelegramClient, chat: str) -> object:
    try:
        return await client.get_entity(peer_reference(chat))
    except ValueError as exc:
        raise ChannelGatewayError(_NOT_FOUND) from exc


async def _channel_participants(
    client: TelegramClient,
    channel: Channel,
    action: ReadChatParticipants,
    now: datetime,
) -> ReadChatParticipantsResult:
    total: int | None = None
    if action.offset == 0 and not action.admins:
        full = await client(GetFullChannelRequest(channel=channel))  # ty: ignore[invalid-argument-type]
        full_chat = getattr(full, "full_chat", None)
        count = getattr(full_chat, "participants_count", None)
        total = count if isinstance(count, int) else None
        hidden = getattr(full_chat, "participants_hidden", False) is True
        if hidden or getattr(full_chat, "can_view_participants", True) is False:
            return ReadChatParticipantsResult(users=[], total=total, hidden=True)
    flt = ChannelParticipantsAdmins() if action.admins else ChannelParticipantsSearch(q="")
    page = await client(
        GetParticipantsRequest(
            channel=channel,  # ty: ignore[invalid-argument-type]
            filter=flt,
            offset=action.offset,
            limit=action.limit,
            hash=0,
        )
    )
    count = getattr(page, "count", None)
    return ReadChatParticipantsResult(
        users=_users(getattr(page, "users", None), now),
        total=count if isinstance(count, int) else total,
    )


async def _basic_group_participants(
    client: TelegramClient,
    chat: Chat,
    action: ReadChatParticipants,
    now: datetime,
) -> ReadChatParticipantsResult:
    """A legacy basic group hands its whole list over at once, so a later page is empty."""
    full = await client(GetFullChatRequest(chat_id=chat.id))
    participants = getattr(getattr(full, "full_chat", None), "participants", None)
    rows = cast("list[object]", getattr(participants, "participants", None) or [])
    users = {user.user_id: user for user in _users(getattr(full, "users", None), now)}
    wanted = [
        int(getattr(row, "user_id", 0))
        for row in rows
        if not action.admins or isinstance(row, ChatParticipantAdmin | ChatParticipantCreator)
    ]
    members = [users[user_id] for user_id in wanted if user_id in users]
    if action.offset > 0:
        return ReadChatParticipantsResult(users=[], total=len(members))
    return ReadChatParticipantsResult(users=members[: action.limit], total=len(members))


async def dispatch_read_chat_participants(
    client: TelegramClient,
    action: ReadChatParticipants,
) -> ReadChatParticipantsResult:
    now = datetime.now(UTC)
    entity = await _entity(client, action.chat)
    if isinstance(entity, Channel):
        return await _channel_participants(client, entity, action, now)
    if isinstance(entity, Chat):
        return await _basic_group_participants(client, entity, action, now)
    raise ChannelGatewayError(_NOT_FOUND)


def _is_reply(message: object, top_msg_id: int | None) -> bool:
    """A reply to another message — not merely "posted in a forum topic".

    Inside a topic every message carries a ``reply_to`` pointing at the topic; only one
    that also names a ``reply_to_top_id`` answers somebody.
    """
    header = getattr(message, "reply_to", None)
    if header is None:
        return False
    if getattr(header, "forum_topic", False) or top_msg_id is not None:
        return getattr(header, "reply_to_top_id", None) is not None
    return True


def _record(message: object, top_msg_id: int | None) -> ChatHistoryMessage:
    sender = getattr(message, "sender_id", None)
    replies = getattr(message, "replies", None)
    count = getattr(replies, "replies", None) if getattr(replies, "comments", False) else None
    date = getattr(message, "date", None)
    return ChatHistoryMessage(
        message_id=int(getattr(message, "id", 0)),
        sender_id=sender if isinstance(sender, int) and sender > 0 else None,
        date=date if isinstance(date, datetime) else datetime.now(UTC),
        is_reply=_is_reply(message, top_msg_id),
        is_forward=getattr(message, "fwd_from", None) is not None,
        replies=count if isinstance(count, int) else None,
        text=str(getattr(message, "message", None) or ""),
    )


def _senders(messages: list[object], now: datetime) -> list[ParsedTelegramUser]:
    seen: dict[int, ParsedTelegramUser] = {}
    for message in messages:
        sender = getattr(message, "sender", None)
        if isinstance(sender, User) and int(sender.id) not in seen:
            seen[int(sender.id)] = parsed_user(sender, now)
    return list(seen.values())


async def _page(
    client: TelegramClient,
    chat: str,
    *,
    limit: int,
    offset_id: int,
    reply_to: int | None,
) -> list[object]:
    try:
        page = await client.get_messages(
            peer_reference(chat), limit=limit, offset_id=offset_id, reply_to=reply_to
        )
    except ValueError as exc:
        raise ChannelGatewayError(_NOT_FOUND) from exc
    # Service messages (joins, pins) carry an ``action`` and no author worth counting.
    return [
        message
        for message in cast("list[object]", page or [])
        if getattr(message, "action", None) is None
    ]


async def dispatch_read_chat_history_authors(
    client: TelegramClient,
    action: ReadChatHistoryAuthors,
) -> ReadChatHistoryAuthorsResult:
    now = datetime.now(UTC)
    raw = await _page(
        client,
        action.chat,
        limit=action.limit,
        offset_id=action.offset_id,
        reply_to=action.top_msg_id,
    )
    messages = [_record(message, action.top_msg_id) for message in raw]
    done = len(raw) < action.limit
    if action.min_date is not None:
        kept = [message for message in messages if message.date >= action.min_date]
        done = done or len(kept) < len(messages)
        keep_ids = {message.message_id for message in kept}
        raw = [message for message in raw if int(getattr(message, "id", 0)) in keep_ids]
        messages = kept
    return ReadChatHistoryAuthorsResult(messages=messages, users=_senders(raw, now), done=done)


async def dispatch_read_channel_post_replies(
    client: TelegramClient,
    action: ReadChannelPostReplies,
) -> ReadChannelPostRepliesResult:
    now = datetime.now(UTC)
    raw = await _page(
        client,
        action.channel,
        limit=action.limit,
        offset_id=action.offset_id,
        reply_to=action.post_id,
    )
    return ReadChannelPostRepliesResult(
        messages=[_record(message, None) for message in raw],
        users=_senders(raw, now),
    )


async def dispatch_user_parser_read(
    client: TelegramClient,
    action: ReadChatParticipants | ReadChatHistoryAuthors | ReadChannelPostReplies,
) -> BaseModel:
    """The family's one entry from ``_read``'s dispatcher."""
    match action:
        case ReadChatParticipants():
            return await dispatch_read_chat_participants(client, action)
        case ReadChatHistoryAuthors():
            return await dispatch_read_chat_history_authors(client, action)
        case ReadChannelPostReplies():
            return await dispatch_read_channel_post_replies(client, action)
