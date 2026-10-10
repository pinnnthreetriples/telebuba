"""Gateway tests for the chat-broadcast family (``core.telegram_client._broadcast``)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from telethon import errors
from telethon.tl.functions.chatlists import (
    CheckChatlistInviteRequest,
    JoinChatlistInviteRequest,
)
from telethon.tl.types import (
    Channel,
    Chat,
    ChatAdminRights,
    ChatBannedRights,
    ChatPhotoEmpty,
    InputPeerChannel,
    PeerChannel,
    TextWithEntities,
)
from telethon.tl.types.chatlists import ChatlistInvite, ChatlistInviteAlready

from core.telegram_client import execute, execute_read
from core.telegram_client._read import TelegramReadError
from schemas.telegram_actions import (
    BroadcastForwardPost,
    BroadcastJoinChatlist,
    BroadcastSendMessage,
    CheckChatlist,
    ListWritableGroups,
)
from schemas.telegram_actions_broadcast import ChatlistResult, WritableGroupsResult
from tests.core.telegram_client.helpers import patch_action_client, patch_read_client

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _channel(  # noqa: PLR0913 - one keyword per Channel field the tests vary
    channel_id: int,
    *,
    megagroup: bool = True,
    username: str | None = None,
    admin_rights: ChatAdminRights | None = None,
    banned_rights: ChatBannedRights | None = None,
    default_banned_rights: ChatBannedRights | None = None,
) -> Channel:
    return Channel(
        id=channel_id,
        title=f"chat {channel_id}",
        photo=ChatPhotoEmpty(),
        date=None,
        megagroup=megagroup,
        broadcast=not megagroup,
        access_hash=channel_id * 10,
        username=username,
        admin_rights=admin_rights,
        banned_rights=banned_rights,
        default_banned_rights=default_banned_rights,
    )


def _closed() -> ChatBannedRights:
    return ChatBannedRights(until_date=None, send_messages=True)


class _Client:
    def __init__(self, responses: dict[type, object] | None = None) -> None:
        self.requests: list[object] = []
        self.sent: list[tuple[object, object, dict[str, object]]] = []
        self.typing: list[object] = []
        self.dialogs: list[object] = []
        self._responses = responses or {}

    async def connect(self) -> None:
        return None

    async def __call__(self, request: object) -> object:
        self.requests.append(request)
        response = self._responses.get(type(request), MagicMock())
        if isinstance(response, Exception):
            raise response
        return response

    @asynccontextmanager
    async def _typing(self, peer: object) -> AsyncIterator[None]:
        self.typing.append(peer)
        yield

    def action(self, peer: object, _kind: str) -> object:
        return self._typing(peer)

    async def send_message(self, peer: object, text: str) -> object:
        self.sent.append((peer, text, {}))
        return MagicMock(id=71)

    async def send_file(self, peer: object, file: object, **kwargs: object) -> object:
        self.sent.append((peer, file, kwargs))
        return MagicMock(id=72)

    async def forward_messages(self, peer: object, message_id: int, **kwargs: object) -> object:
        self.sent.append((peer, message_id, kwargs))
        return [MagicMock(id=73)]

    async def iter_dialogs(self, *, limit: int) -> AsyncIterator[object]:
        for entity in self.dialogs[:limit]:
            yield MagicMock(entity=entity)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr("core.telegram_client._broadcast.asyncio.sleep", instant)


@pytest.mark.asyncio
async def test_send_text_types_first_and_returns_message_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client()
    patch_action_client(monkeypatch, client)

    result = await execute(
        "acc", BroadcastSendMessage(chat="1234", text="hello", typing_seconds=2.0), domain="cb"
    )

    assert result.status == "ok"
    assert result.message_id == 71
    assert client.typing == [1234]
    assert client.sent == [(1234, "hello", {})]


@pytest.mark.asyncio
async def test_send_photo_uses_text_as_caption(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _Client()
    patch_action_client(monkeypatch, client)

    result = await execute(
        "acc", BroadcastSendMessage(chat="alpha", text="cap", photo=b"img", photo_name="a.png")
    )

    assert result.message_id == 72
    peer, file, kwargs = client.sent[0]
    assert peer == "alpha"
    assert getattr(file, "name", None) == "a.png"
    assert kwargs == {"caption": "cap"}
    assert client.typing == []


@pytest.mark.asyncio
async def test_forward_post_reports_forwarded_id(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _Client()
    patch_action_client(monkeypatch, client)

    result = await execute(
        "acc", BroadcastForwardPost(chat="555", channel="mychannel", message_id=9)
    )

    assert result.status == "ok"
    assert result.message_id == 73
    assert client.sent == [(555, 9, {"from_peer": "mychannel"})]


@pytest.mark.asyncio
async def test_join_chatlist_joins_only_the_named_peer(monkeypatch: pytest.MonkeyPatch) -> None:
    invite = ChatlistInvite(
        title=TextWithEntities(text="Folder", entities=[]),
        peers=[PeerChannel(5), PeerChannel(6)],
        chats=[_channel(5), _channel(6)],
        users=[],
    )
    client = _Client({CheckChatlistInviteRequest: invite})
    patch_action_client(monkeypatch, client)

    result = await execute("acc", BroadcastJoinChatlist(slug="abc", peer_id=6))

    assert result.status == "ok"
    join = client.requests[-1]
    assert isinstance(join, JoinChatlistInviteRequest)
    assert join.peers == [InputPeerChannel(channel_id=6, access_hash=60)]


@pytest.mark.asyncio
async def test_join_chatlist_already_inside_is_already_participant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invite = ChatlistInviteAlready(
        filter_id=1, missing_peers=[], already_peers=[PeerChannel(6)], chats=[], users=[]
    )
    client = _Client({CheckChatlistInviteRequest: invite})
    patch_action_client(monkeypatch, client)

    result = await execute("acc", BroadcastJoinChatlist(slug="abc", peer_id=6))

    assert result.status == "already_participant"


@pytest.mark.asyncio
async def test_join_chatlist_peer_missing_from_folder_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invite = ChatlistInvite(
        title=TextWithEntities(text="F", entities=[]), peers=[], chats=[], users=[]
    )
    patch_action_client(monkeypatch, _Client({CheckChatlistInviteRequest: invite}))

    result = await execute("acc", BroadcastJoinChatlist(slug="abc", peer_id=6))

    assert result.status == "failed"
    assert result.error_message == "chat_not_found"


@pytest.mark.asyncio
async def test_join_chatlist_request_sent_is_join_by_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invite = ChatlistInvite(
        title=TextWithEntities(text="F", entities=[]),
        peers=[PeerChannel(6)],
        chats=[_channel(6)],
        users=[],
    )
    client = _Client(
        {
            CheckChatlistInviteRequest: invite,
            JoinChatlistInviteRequest: errors.InviteRequestSentError(request=None),
        }
    )
    patch_action_client(monkeypatch, client)

    result = await execute("acc", BroadcastJoinChatlist(slug="abc", peer_id=6))

    assert result.error_type == "InviteRequestSentError"


@pytest.mark.asyncio
async def test_list_writable_groups_sorts_out_channels_and_admin_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client()
    client.dialogs = [
        _channel(1, username="open"),
        _channel(2, megagroup=False),
        _channel(3, default_banned_rights=_closed()),
        _channel(
            4,
            default_banned_rights=_closed(),
            admin_rights=ChatAdminRights(),
        ),
        _channel(5, banned_rights=_closed()),
        Chat(
            id=6, title="basic", photo=ChatPhotoEmpty(), participants_count=3, date=None, version=1
        ),
        Chat(
            id=7,
            title="old",
            photo=ChatPhotoEmpty(),
            participants_count=3,
            date=None,
            version=1,
            deactivated=True,
        ),
        MagicMock(spec=[]),
    ]
    patch_read_client(monkeypatch, client)

    result = await execute_read("acc", ListWritableGroups())

    assert isinstance(result, WritableGroupsResult)
    assert [(g.peer_id, g.username) for g in result.groups] == [(1, "open"), (4, None), (6, None)]
    assert result.channels_skipped == 1
    assert result.admin_only_skipped == 2


@pytest.mark.asyncio
async def test_check_chatlist_lists_groups_and_counts_channels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invite = ChatlistInvite(
        title=TextWithEntities(text="Crypto", entities=[]),
        peers=[],
        chats=[_channel(5), _channel(6, megagroup=False), _channel(7)],
        users=[],
    )
    patch_read_client(monkeypatch, _Client({CheckChatlistInviteRequest: invite}))

    result = await execute_read("acc", CheckChatlist(slug="abc"))

    assert isinstance(result, ChatlistResult)
    assert result.title == "Crypto"
    assert [g.peer_id for g in result.groups] == [5, 7]
    assert result.channels_skipped == 1


@pytest.mark.asyncio
async def test_check_chatlist_expired_slug_is_read_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _Client(
        {
            CheckChatlistInviteRequest: errors.BadRequestError(
                request=None, message="INVITE_SLUG_EXPIRED"
            )
        }
    )
    patch_read_client(monkeypatch, client)

    with pytest.raises(TelegramReadError, match="BadRequest"):
        await execute_read("acc", CheckChatlist(slug="abc"))
