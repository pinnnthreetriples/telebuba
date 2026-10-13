"""The partner-facing warming actions (items 4.6/4.7) on a fake client.

A post's comments, a DM reaction, a sticker, a forward; no real account is touched.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from telethon.tl.functions.messages import (
    GetRepliesRequest,
    GetStickersRequest,
    ReadDiscussionRequest,
    SendReactionRequest,
)

from core.telegram_client import _warm_social
from core.telegram_client._warm_dispatch import warm_log_extra
from schemas.telegram_actions_warming_social import (
    WarmDmForward,
    WarmDmReact,
    WarmDmSticker,
    WarmReadDiscussion,
)

if TYPE_CHECKING:
    from telethon import TelegramClient


class _Client:
    def __init__(self, *, stickers: list[object] | None = None) -> None:
        self.requests: list[object] = []
        self.sent: list[tuple[str, object]] = []
        self.stickers = stickers or []
        self.history: list[SimpleNamespace] = [
            SimpleNamespace(id=9, out=True),
            SimpleNamespace(id=8, out=False),
        ]

    async def __call__(self, request: object) -> object:
        self.requests.append(request)
        if isinstance(request, GetRepliesRequest):
            return SimpleNamespace(messages=[SimpleNamespace(id=31), SimpleNamespace(id=35)])
        if isinstance(request, GetStickersRequest):
            return SimpleNamespace(stickers=self.stickers)
        return None

    async def get_input_entity(self, peer: object) -> object:
        return peer

    async def get_messages(self, _peer: object, limit: int) -> list[SimpleNamespace]:
        assert limit >= 1
        return self.history

    async def send_file(self, _peer: object, file: object) -> object:
        self.sent.append(("file", file))
        return SimpleNamespace(id=50)

    async def send_message(self, _peer: object, text: str) -> object:
        self.sent.append(("text", text))
        return SimpleNamespace(id=51)

    async def forward_messages(
        self, _peer: object, message_id: int, from_peer: str
    ) -> list[object]:
        self.sent.append(("forward", (from_peer, message_id)))
        return [SimpleNamespace(id=52)]


def _as_client(client: _Client) -> TelegramClient:
    return cast("TelegramClient", client)


@pytest.fixture(autouse=True)
def _peer_and_group(monkeypatch: pytest.MonkeyPatch) -> None:
    async def resolve(_client: object, action: object) -> object:
        return f"user:{getattr(action, 'user_id', 0)}"

    async def linked(_client: object, channel: str) -> object | None:
        return None if channel == "@no_comments" else object()

    monkeypatch.setattr(_warm_social, "_resolve_dm_peer", resolve)
    monkeypatch.setattr(_warm_social, "_resolve_linked_group_entity", linked)


@pytest.mark.asyncio
async def test_the_discussion_is_read_without_joining_the_group() -> None:
    client = _Client()
    result = await _warm_social.read_discussion(
        _as_client(client), WarmReadDiscussion(channel="@news", message_id=7)
    )
    kinds = [type(r) for r in client.requests]
    assert kinds == [GetRepliesRequest, ReadDiscussionRequest]
    read = client.requests[1]
    assert isinstance(read, ReadDiscussionRequest)
    assert read.read_max_id == 35
    assert result.log_extra == {"comments": 2}


@pytest.mark.asyncio
async def test_a_post_without_comments_is_a_skip() -> None:
    client = _Client()
    result = await _warm_social.read_discussion(
        _as_client(client), WarmReadDiscussion(channel="@no_comments", message_id=7)
    )
    assert client.requests == []
    assert result.log_extra == {"warm_skip": "no_discussion"}


@pytest.mark.asyncio
async def test_the_reaction_lands_on_the_partners_newest_message() -> None:
    client = _Client()
    result = await _warm_social.dm_react(_as_client(client), WarmDmReact(user_id=5, emoji="😂"))
    reaction = client.requests[0]
    assert isinstance(reaction, SendReactionRequest)
    assert reaction.msg_id == 8
    assert result.message_id == 8


@pytest.mark.asyncio
async def test_no_incoming_message_means_no_reaction() -> None:
    client = _Client()
    client.history = [SimpleNamespace(id=9, out=True)]
    result = await _warm_social.dm_react(_as_client(client), WarmDmReact(user_id=5, emoji="👍"))
    assert client.requests == []
    assert result.log_extra == {"warm_skip": "no_message"}


@pytest.mark.asyncio
async def test_a_sticker_comes_from_the_installed_sets_only() -> None:
    empty = _Client()
    skipped = await _warm_social.dm_sticker(
        _as_client(empty), WarmDmSticker(user_id=5, emoticon="😂")
    )
    assert skipped.log_extra == {"warm_skip": "no_sticker"}
    assert empty.sent == []

    client = _Client(stickers=["s1", "s2"])
    sent = await _warm_social.dm_sticker(
        _as_client(client), WarmDmSticker(user_id=5, emoticon="😂", pick=3)
    )
    assert client.sent == [("file", "s2")]
    assert sent.log_extra == {"emoticon": "😂"}


@pytest.mark.asyncio
async def test_the_forward_carries_its_word_and_logs_no_text() -> None:
    client = _Client()
    action = WarmDmForward(user_id=5, channel="@news", message_id=4, comment="mira")
    result = await _warm_social.dm_forward(_as_client(client), action)
    assert client.sent == [("forward", ("@news", 4)), ("text", "mira")]
    assert result.message_id == 52
    assert warm_log_extra(action) == {"channel": "@news", "source_id": 4}
    assert warm_log_extra(WarmDmReact(user_id=5, emoji="😂")) == {}
