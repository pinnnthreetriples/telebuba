"""Chat-scoped reads: resolving a target per account, and reading messages back."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from telethon.tl.functions.messages import CheckChatInviteRequest
from telethon.tl.types import (
    Channel,
    Chat,
    MessageMediaDocument,
    MessageMediaEmpty,
    MessageMediaPhoto,
    MessageMediaUnsupported,
    MessageMediaWebPage,
    User,
)

from core.telegram_client import TelegramReadError, execute_read
from core.telegram_client._read_chat import media_kind, peer_reference
from schemas.telegram_actions import ReadChatMessages, ResolveChat
from tests.core.telegram_client.helpers import patch_read_client as _patch_client

if TYPE_CHECKING:
    from schemas.telegram_actions import ReadChatMessagesResult, ResolveChatResult


def _channel(*, megagroup: bool) -> Channel:
    return Channel(
        id=777,
        title="Target",
        photo=None,  # ty: ignore[invalid-argument-type]
        date=None,
        megagroup=megagroup,
    )


class _ResolveClient:
    """Answers ``get_entity`` with a fixed entity and records what was asked."""

    def __init__(self, entity: object) -> None:
        self.entity = entity
        self.asked: list[object] = []

    async def connect(self) -> None:
        return None

    async def get_entity(self, target: object) -> object:
        self.asked.append(target)
        if isinstance(self.entity, Exception):
            raise self.entity
        return self.entity


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entity", "kind"),
    [
        (_channel(megagroup=True), "megagroup"),
        (_channel(megagroup=False), "channel"),
    ],
)
async def test_a_public_target_resolves_to_this_accounts_own_chat_id(
    monkeypatch: pytest.MonkeyPatch,
    entity: object,
    kind: str,
) -> None:
    client = _ResolveClient(entity)
    _patch_client(monkeypatch, client)

    result: ResolveChatResult = await execute_read("acc-1", ResolveChat(target="target"))  # ty: ignore[invalid-assignment]

    assert (result.chat_id, result.kind) == (777, kind)
    assert client.asked == ["target"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entity", "kind"),
    [
        (
            Chat(
                id=42,
                title="Old group",
                photo=None,  # ty: ignore[invalid-argument-type]
                participants_count=3,
                date=None,
                version=1,
            ),
            "basic_group",
        ),
        (User(id=99, first_name="Someone"), "user"),
    ],
)
async def test_a_shared_id_sequence_peer_is_reported_by_kind_not_hidden(
    monkeypatch: pytest.MonkeyPatch,
    entity: object,
    kind: str,
) -> None:
    """Basic groups and private chats resolve fine — the DOMAIN is what refuses them.

    Their message ids are per-user, so a cross-account reply chain misfires silently.
    The gateway's job is only to say which shape it found.
    """
    _patch_client(monkeypatch, _ResolveClient(entity))

    result: ResolveChatResult = await execute_read("acc-1", ResolveChat(target="target"))  # ty: ignore[invalid-assignment]

    assert result.kind == kind


@pytest.mark.asyncio
async def test_an_unresolvable_target_never_escapes_as_a_bare_value_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telethon answers an unknown peer with ``ValueError``, which is not in the ladder.

    Left alone it would leave the gateway raw and reach services as a 500 carrying
    Telethon's English prose — the exact hole ``ChannelGatewayError`` closed for the
    channel reads.
    """
    _patch_client(monkeypatch, _ResolveClient(ValueError("No user has 'target' as username")))

    with pytest.raises(TelegramReadError) as refusal:
        await execute_read("acc-1", ResolveChat(target="target"))

    assert refusal.value.reason == "chat_not_found"


@pytest.mark.asyncio
async def test_a_peer_with_no_usable_id_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _ResolveClient(_channel_without_id()))

    with pytest.raises(TelegramReadError) as refusal:
        await execute_read("acc-1", ResolveChat(target="target"))

    assert refusal.value.reason == "chat_not_found"


def _channel_without_id() -> Channel:
    channel = _channel(megagroup=True)
    channel.id = 0
    return channel


@pytest.mark.asyncio
async def test_an_unknown_peer_shape_is_refused_rather_than_guessed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_client(monkeypatch, _ResolveClient(MagicMock(id=5)))

    with pytest.raises(TelegramReadError) as refusal:
        await execute_read("acc-1", ResolveChat(target="target"))

    assert refusal.value.reason == "chat_not_found"


class _InviteClient:
    def __init__(self, invite: object) -> None:
        self.invite = invite
        self.requests: list[object] = []

    async def connect(self) -> None:
        return None

    async def __call__(self, request: object) -> object:
        self.requests.append(request)
        return self.invite


@pytest.mark.asyncio
async def test_a_private_invite_resolves_only_once_the_account_is_inside(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _InviteClient(MagicMock(chat=_channel(megagroup=True)))
    _patch_client(monkeypatch, client)

    result: ResolveChatResult = await execute_read("acc-1", ResolveChat(target="+ABCDEFGH"))  # ty: ignore[invalid-assignment]

    assert result.chat_id == 777
    assert isinstance(client.requests[0], CheckChatInviteRequest)
    assert client.requests[0].hash == "ABCDEFGH"


@pytest.mark.asyncio
async def test_an_invite_preview_is_not_a_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """A not-yet-joined account gets a preview with no chat in it.

    Nothing in that preview can be sent to, so treating it as success would let the
    engine "resolve" a chat the account has never entered.
    """
    _patch_client(monkeypatch, _InviteClient(MagicMock(chat=None)))

    with pytest.raises(TelegramReadError) as refusal:
        await execute_read("acc-1", ResolveChat(target="+ABCDEFGH"))

    assert refusal.value.reason == "chat_not_found"


class _MessagesClient:
    def __init__(self, messages: list[object | None]) -> None:
        self.messages = messages
        self.peers: list[object] = []

    async def connect(self) -> None:
        return None

    async def get_messages(self, peer: object, *, ids: list[int]) -> list[object | None]:
        self.peers.append(peer)
        assert len(ids) == len(self.messages)
        return self.messages


@pytest.mark.asyncio
async def test_reading_messages_reports_media_by_kind_and_names_what_is_invisible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _MessagesClient(
        [MagicMock(message="hello", media=MessageMediaPhoto(photo=None)), None],
    )
    _patch_client(monkeypatch, client)

    result: ReadChatMessagesResult = await execute_read(  # ty: ignore[invalid-assignment]
        "acc-1",
        ReadChatMessages(chat="1234", message_ids=[10, 11]),
    )

    assert [(m.message_id, m.text, m.media_kind) for m in result.messages] == [
        (10, "hello", "photo"),
    ]
    assert result.missing_ids == [11]
    # All-digit chat references go down as ints so the session entity cache answers
    # them; as a string Telethon would send the same digits to the username resolver.
    assert client.peers == [1234]


@pytest.mark.asyncio
async def test_an_unreachable_chat_is_refused_with_a_stable_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unknown_peer = ValueError("Could not find the input entity")

    class _Failing(_MessagesClient):
        async def get_messages(
            self,
            peer: object,  # noqa: ARG002 - signature parity with the real client
            *,
            ids: list[int],  # noqa: ARG002 - signature parity with the real client
        ) -> list[object | None]:
            raise unknown_peer

    _patch_client(monkeypatch, _Failing([]))

    with pytest.raises(TelegramReadError) as refusal:
        await execute_read("acc-1", ReadChatMessages(chat="@group", message_ids=[1]))

    assert refusal.value.reason == "chat_not_found"


class _CursorClient:
    """Answers the cursor form of ``get_messages`` and records how it was asked."""

    def __init__(self, messages: list[object]) -> None:
        self.messages = messages
        self.calls: list[tuple[object, int, int]] = []

    async def connect(self) -> None:
        return None

    async def get_messages(self, peer: object, *, limit: int, min_id: int) -> list[object]:
        self.calls.append((peer, limit, min_id))
        return self.messages


def _message(
    message_id: int, text: str, *, out: bool = False, sender: int | None = 42
) -> MagicMock:
    return MagicMock(id=message_id, message=text, media=None, out=out, sender_id=sender)


@pytest.mark.asyncio
async def test_the_cursor_form_asks_for_the_newest_page_and_answers_oldest_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Newest-first from Telegram, oldest-first to the caller.

    The direction is not cosmetic. ``get_messages(limit=...)`` walks BACK from the
    head of the chat, which is what makes ``min_id=0`` mean "the latest page"
    instead of "the beginning of history"; the caller then needs the conversation
    in the order it was said, and a cursor only advances safely by the last id.
    """
    client = _CursorClient([_message(9, "later"), _message(7, "earlier")])
    _patch_client(monkeypatch, client)

    result: ReadChatMessagesResult = await execute_read(  # ty: ignore[invalid-assignment]
        "acc-1",
        ReadChatMessages(chat="1234", min_id=5, limit=20),
    )

    assert [(m.message_id, m.text) for m in result.messages] == [(7, "earlier"), (9, "later")]
    assert result.missing_ids == []
    assert client.calls == [(1234, 20, 5)]


@pytest.mark.asyncio
async def test_the_cursor_form_reports_the_sender_and_whether_we_wrote_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_client(
        monkeypatch,
        _CursorClient([_message(7, "mine", out=True), _message(8, "theirs", sender=None)]),
    )

    result: ReadChatMessagesResult = await execute_read(  # ty: ignore[invalid-assignment]
        "acc-1",
        ReadChatMessages(chat="1234", min_id=0),
    )

    assert [(m.message_id, m.outgoing, m.sender_id) for m in result.messages] == [
        (7, True, 42),
        (8, False, None),
    ]


def test_a_read_names_exactly_one_mode() -> None:
    """Neither mode and both modes are refused, and for opposite reasons.

    With neither, the dispatcher would have to invent a default and read a whole
    chat nobody asked for; with both, it would have to pick one silently.
    """
    with pytest.raises(ValidationError):
        ReadChatMessages(chat="1234")
    with pytest.raises(ValidationError):
        ReadChatMessages(chat="1234", message_ids=[1], min_id=0)


@pytest.mark.parametrize(
    ("media", "expected"),
    [
        (None, "none"),
        (MessageMediaPhoto(photo=None), "photo"),
        (MessageMediaDocument(document=None), "document"),
        (MessageMediaWebPage(webpage=None), "web_page"),  # ty: ignore[invalid-argument-type]
        (MessageMediaEmpty(), "unsupported"),
        (MessageMediaUnsupported(), "unsupported"),
    ],
)
def test_media_is_classified_by_its_concrete_class(media: object, expected: str) -> None:
    """``is not None`` is not enough, and that is the whole point of the allow-list.

    An empty or unsupported union is ACCEPTED by ``send_file`` and produces a message
    with no media at all, while a web-page preview raises — so "there is media here"
    answers neither question the copy path needs answered.
    """
    assert media_kind(media) == expected


@pytest.mark.parametrize(("value", "expected"), [("1234", 1234), ("@name", "@name")])
def test_a_digit_only_chat_reference_goes_down_as_an_id(value: str, expected: object) -> None:
    assert peer_reference(value) == expected
