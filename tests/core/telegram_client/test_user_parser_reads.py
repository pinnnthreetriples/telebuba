"""User-parser reads through ``execute_read``: members, history authors, post comments."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from telethon import errors
from telethon.tl.functions.channels import GetFullChannelRequest, GetParticipantsRequest
from telethon.tl.functions.messages import GetFullChatRequest
from telethon.tl.types import (
    Channel,
    ChannelParticipantsAdmins,
    Chat,
    ChatParticipant,
    ChatParticipantAdmin,
    User,
    Username,
    UserStatusEmpty,
    UserStatusLastMonth,
    UserStatusLastWeek,
    UserStatusOffline,
    UserStatusOnline,
    UserStatusRecently,
)

from core.telegram_client import TelegramReadError, execute_read
from core.telegram_client._read_user_parser import parsed_user
from schemas.telegram_actions import (
    ReadChannelPostReplies,
    ReadChatHistoryAuthors,
    ReadChatParticipants,
)
from tests.core.telegram_client.helpers import patch_read_client

if TYPE_CHECKING:
    from schemas.telegram_actions_user_parser import (
        ReadChannelPostRepliesResult,
        ReadChatHistoryAuthorsResult,
        ReadChatParticipantsResult,
    )

_NOW = datetime(2026, 10, 9, 12, tzinfo=UTC)


def _user(user_id: int, **fields: Any) -> User:
    return User(id=user_id, first_name=f"U{user_id}", **fields)


def _channel(*, megagroup: bool = True) -> Channel:
    return Channel(
        id=777,
        title="Target",
        photo=None,  # ty: ignore[invalid-argument-type]
        date=None,
        megagroup=megagroup,
    )


class _Client:
    """Answers the calls the user-parser dispatchers make, and records the requests."""

    def __init__(self, entity: object, replies: dict[type, object] | None = None) -> None:
        self.entity = entity
        self.replies = replies or {}
        self.requests: list[object] = []
        self.pages: list[list[object]] = []
        self.asked_messages: list[dict[str, Any]] = []
        self.error: Exception | None = None

    async def connect(self) -> None:
        return None

    async def get_entity(self, _target: object) -> object:
        if isinstance(self.entity, Exception):
            raise self.entity
        return self.entity

    async def __call__(self, request: object) -> object:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.replies[type(request)]

    async def get_messages(self, peer: object, **kwargs: Any) -> list[object]:
        self.asked_messages.append({"peer": peer, **kwargs})
        if self.error is not None:
            raise self.error
        return self.pages.pop(0) if self.pages else []


def _full(**full_chat: object) -> SimpleNamespace:
    return SimpleNamespace(full_chat=SimpleNamespace(**full_chat))


@pytest.mark.parametrize(
    ("status", "bucket"),
    [
        (UserStatusOnline(expires=_NOW), "online"),
        (UserStatusRecently(), "recently"),
        (UserStatusLastWeek(), "week"),
        (UserStatusLastMonth(), "month"),
        (UserStatusEmpty(), "long"),
        (None, "hidden"),
        (UserStatusOffline(was_online=_NOW - timedelta(days=1)), "recently"),
        (UserStatusOffline(was_online=_NOW - timedelta(days=5)), "week"),
        (UserStatusOffline(was_online=_NOW - timedelta(days=20)), "month"),
        (UserStatusOffline(was_online=_NOW - timedelta(days=90)), "long"),
        (UserStatusOffline(was_online=None), "long"),
    ],
)
def test_last_seen_is_reported_as_one_bucket(status: object, bucket: str) -> None:
    assert parsed_user(_user(1, status=status), _NOW).last_seen == bucket


def test_a_user_record_carries_profile_facts_and_never_the_phone() -> None:
    user = _user(
        5,
        username=None,
        usernames=[Username(username="old", active=False), Username(username="nft", active=True)],
        last_name="L",
        bot=True,
        premium=True,
        scam=True,
        photo=SimpleNamespace(),
        stories_max_id=3,
        phone="79990001122",
        access_hash=42,
    )

    record = parsed_user(user, _NOW)

    assert record.username == "nft"
    assert (record.is_bot, record.is_premium, record.is_scam, record.is_fake) == (
        True,
        True,
        True,
        False,
    )
    assert (record.has_photo, record.has_stories) == (True, True)
    assert "phone" not in record.model_dump()
    assert "79990001122" not in record.model_dump_json()
    hidden_stories = _user(6, stories_max_id=3, stories_unavailable=True)
    assert parsed_user(hidden_stories, _NOW).has_stories is False


@pytest.mark.asyncio
async def test_the_first_members_page_reports_the_total_and_the_users(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = SimpleNamespace(count=2, users=[_user(1), _user(2), SimpleNamespace(id=3)])
    client = _Client(
        _channel(),
        {GetFullChannelRequest: _full(participants_count=9), GetParticipantsRequest: page},
    )
    patch_read_client(monkeypatch, client)

    result: ReadChatParticipantsResult = await execute_read(  # ty: ignore[invalid-assignment]
        "acc", ReadChatParticipants(chat="group", limit=50)
    )

    assert [user.user_id for user in result.users] == [1, 2]
    assert (result.total, result.hidden) == (2, False)
    request = client.requests[-1]
    assert isinstance(request, GetParticipantsRequest)
    assert (request.offset, request.limit) == (0, 50)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "full_chat",
    [
        {"participants_hidden": True, "participants_count": 500},
        {"can_view_participants": False, "participants_count": 500},
    ],
)
async def test_a_hidden_member_list_is_said_so_instead_of_read_empty(
    monkeypatch: pytest.MonkeyPatch, full_chat: dict[str, object]
) -> None:
    client = _Client(_channel(), {GetFullChannelRequest: _full(**full_chat)})
    patch_read_client(monkeypatch, client)

    result: ReadChatParticipantsResult = await execute_read(  # ty: ignore[invalid-assignment]
        "acc", ReadChatParticipants(chat="group")
    )

    assert (result.hidden, result.users, result.total) == (True, [], 500)
    assert not any(isinstance(r, GetParticipantsRequest) for r in client.requests)


@pytest.mark.asyncio
async def test_a_later_page_and_the_admin_list_skip_the_hidden_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = SimpleNamespace(count=None, users=[_user(9)])
    client = _Client(_channel(), {GetParticipantsRequest: page})
    patch_read_client(monkeypatch, client)

    later: ReadChatParticipantsResult = await execute_read(  # ty: ignore[invalid-assignment]
        "acc", ReadChatParticipants(chat="group", offset=200)
    )
    admins: ReadChatParticipantsResult = await execute_read(  # ty: ignore[invalid-assignment]
        "acc", ReadChatParticipants(chat="group", admins=True)
    )

    assert [u.user_id for u in later.users] == [9]
    assert later.total is None
    assert isinstance(client.requests[-1].filter, ChannelParticipantsAdmins)  # ty: ignore[unresolved-attribute]
    assert [u.user_id for u in admins.users] == [9]
    assert not any(isinstance(r, GetFullChannelRequest) for r in client.requests)


@pytest.mark.asyncio
async def test_a_basic_group_hands_its_whole_list_over_on_the_first_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        ChatParticipant(user_id=1, inviter_id=1, date=_NOW),
        ChatParticipantAdmin(user_id=2, inviter_id=1, date=_NOW),
    ]
    full = SimpleNamespace(
        full_chat=SimpleNamespace(participants=SimpleNamespace(participants=rows)),
        users=[_user(1), _user(2)],
    )
    chat = Chat(id=55, title="Old", photo=None, participants_count=2, date=_NOW, version=1)  # ty: ignore[invalid-argument-type]
    client = _Client(chat, {GetFullChatRequest: full})
    patch_read_client(monkeypatch, client)

    first: ReadChatParticipantsResult = await execute_read(  # ty: ignore[invalid-assignment]
        "acc", ReadChatParticipants(chat="55")
    )
    admins: ReadChatParticipantsResult = await execute_read(  # ty: ignore[invalid-assignment]
        "acc", ReadChatParticipants(chat="55", admins=True)
    )
    later: ReadChatParticipantsResult = await execute_read(  # ty: ignore[invalid-assignment]
        "acc", ReadChatParticipants(chat="55", offset=200)
    )

    assert ([u.user_id for u in first.users], first.total) == ([1, 2], 2)
    assert [u.user_id for u in admins.users] == [2]
    assert (later.users, later.total) == ([], 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("entity", [ValueError("nope"), _user(1)])
async def test_a_peer_that_is_no_group_is_chat_not_found(
    monkeypatch: pytest.MonkeyPatch, entity: object
) -> None:
    patch_read_client(monkeypatch, _Client(entity))

    with pytest.raises(TelegramReadError) as refused:
        await execute_read("acc", ReadChatParticipants(chat="ghost"))

    assert (refused.value.reason, refused.value.kind) == ("chat_not_found", "other")


def _message(message_id: int, sender: object, **fields: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": message_id,
        "sender": sender,
        "sender_id": getattr(sender, "id", None),
        "date": _NOW - timedelta(hours=message_id),
        "reply_to": None,
        "fwd_from": None,
        "replies": None,
        "message": f"text {message_id}",
        "action": None,
    }
    return SimpleNamespace(**(base | fields))


@pytest.mark.asyncio
async def test_a_history_page_lists_messages_and_their_distinct_authors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    author = _user(10)
    client = _Client(_channel())
    client.pages = [
        [
            _message(1, author, reply_to=SimpleNamespace(forum_topic=False)),
            _message(2, author, fwd_from=SimpleNamespace()),
            _message(3, SimpleNamespace(id=-100), sender_id=-100),
            _message(4, None, action=SimpleNamespace()),
        ]
    ]
    patch_read_client(monkeypatch, client)

    result: ReadChatHistoryAuthorsResult = await execute_read(  # ty: ignore[invalid-assignment]
        "acc", ReadChatHistoryAuthors(chat="group", offset_id=50, limit=10)
    )

    assert [m.message_id for m in result.messages] == [1, 2, 3]
    assert [(m.is_reply, m.is_forward) for m in result.messages[:2]] == [
        (True, False),
        (False, True),
    ]
    assert result.messages[2].sender_id is None
    assert [u.user_id for u in result.users] == [10]
    assert result.done is True
    assert client.asked_messages[0] == {
        "peer": "group",
        "limit": 10,
        "offset_id": 50,
        "reply_to": None,
    }


@pytest.mark.asyncio
async def test_the_walk_ends_at_min_date_and_a_topic_message_is_no_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old, new = _user(1), _user(2)
    topic = SimpleNamespace(forum_topic=True, reply_to_top_id=None)
    answer = SimpleNamespace(forum_topic=True, reply_to_top_id=123)
    client = _Client(_channel())
    client.pages = [
        [
            _message(1, new, reply_to=topic),
            _message(2, new, reply_to=answer),
            _message(30, old, reply_to=topic),
        ]
    ]
    patch_read_client(monkeypatch, client)

    result: ReadChatHistoryAuthorsResult = await execute_read(  # ty: ignore[invalid-assignment]
        "acc",
        ReadChatHistoryAuthors(
            chat="123", limit=3, top_msg_id=123, min_date=_NOW - timedelta(days=1)
        ),
    )

    assert [(m.message_id, m.is_reply) for m in result.messages] == [(1, False), (2, True)]
    assert [u.user_id for u in result.users] == [2]
    assert result.done is True
    assert client.asked_messages[0]["peer"] == 123
    assert client.asked_messages[0]["reply_to"] == 123


@pytest.mark.asyncio
async def test_channel_posts_report_their_comment_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _Client(_channel(megagroup=False))
    client.pages = [
        [
            _message(7, None, replies=SimpleNamespace(comments=True, replies=12)),
            _message(6, None, replies=SimpleNamespace(comments=False, replies=3)),
        ]
    ]
    patch_read_client(monkeypatch, client)

    result: ReadChatHistoryAuthorsResult = await execute_read(  # ty: ignore[invalid-assignment]
        "acc", ReadChatHistoryAuthors(chat="news", limit=2)
    )

    assert [m.replies for m in result.messages] == [12, None]
    assert result.done is False


@pytest.mark.asyncio
async def test_post_comments_come_with_their_authors(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _Client(_channel(megagroup=False))
    client.pages = [[_message(101, _user(4)), _message(100, _user(5))]]
    patch_read_client(monkeypatch, client)

    result: ReadChannelPostRepliesResult = await execute_read(  # ty: ignore[invalid-assignment]
        "acc", ReadChannelPostReplies(channel="news", post_id=7, offset_id=200, limit=2)
    )

    assert [(m.message_id, m.sender_id) for m in result.messages] == [(101, 4), (100, 5)]
    assert [u.user_id for u in result.users] == [4, 5]
    assert client.asked_messages[0] == {
        "peer": "news",
        "limit": 2,
        "offset_id": 200,
        "reply_to": 7,
    }


@pytest.mark.asyncio
async def test_an_unknown_peer_in_a_page_read_is_chat_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client(_channel())
    client.error = ValueError("Could not find the input entity")
    patch_read_client(monkeypatch, client)

    with pytest.raises(TelegramReadError) as refused:
        await execute_read("acc", ReadChannelPostReplies(channel="news", post_id=1))

    assert refused.value.reason == "chat_not_found"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "kind", "seconds"),
    [
        (errors.FloodWaitError(request=None, capture=42), "flood_wait", 42),
        (errors.PeerFloodError(request=None), "flood_wait", None),
        (errors.ChatAdminRequiredError(request=None), "other", None),
    ],
)
async def test_read_failures_are_classified_by_the_gateway(
    monkeypatch: pytest.MonkeyPatch, error: Exception, kind: str, seconds: int | None
) -> None:
    client = _Client(_channel())
    client.error = error
    patch_read_client(monkeypatch, client)

    with pytest.raises(TelegramReadError) as refused:
        await execute_read("acc", ReadChatHistoryAuthors(chat="group"))
    with pytest.raises(TelegramReadError) as members:
        await execute_read("acc", ReadChatParticipants(chat="group"))

    assert (refused.value.kind, refused.value.seconds) == (kind, seconds)
    assert (members.value.kind, members.value.seconds) == (kind, seconds)
