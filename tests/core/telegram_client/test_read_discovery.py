"""Channel-discovery read dispatchers — native Telegram channel search."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from telethon import errors
from telethon.tl.functions.channels import GetChannelRecommendationsRequest
from telethon.tl.functions.contacts import SearchRequest
from telethon.tl.functions.messages import SearchGlobalRequest
from telethon.tl.types import InputPeerEmpty

from core.telegram_client import TelegramReadError, execute_read
from schemas.telegram_actions import (
    GetLinkedDiscussionGroup,
    GetSimilarChannels,
    LinkedDiscussionGroupResult,
    SearchChannels,
    SearchGlobalPosts,
)
from schemas.telegram_actions_discovery import (
    GlobalPostsCursor,
    TelegramChannelMatches,
    TelegramGlobalPostMatches,
)
from tests.core.telegram_client.helpers import patch_read_client as _patch_client


def _channel(
    username: str | None,
    *,
    title: str = "T",
    broadcast: bool = True,
    participants_count: int | None = None,
    channel_id: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=channel_id,
        username=username,
        title=title,
        broadcast=broadcast,
        participants_count=participants_count,
    )


class _FakeClient:
    """Records what it was asked for; ``messages``/``next_rate`` are searchGlobal's extras."""

    def __init__(
        self,
        chats: list[object],
        messages: list[object] | None = None,
        *,
        next_rate: int | None = None,
    ) -> None:
        self.chats = chats
        self.messages = messages
        self.next_rate = next_rate
        self.requests: list[object] = []
        self.resolved: list[str] = []

    async def connect(self) -> None:
        return None

    # Positional-only: subclasses below rename these to _-prefixed unused params,
    # which an override check would otherwise reject.
    async def get_input_entity(self, handle: str, /) -> object:
        self.resolved.append(handle)
        return SimpleNamespace(channel_id=1)

    async def __call__(self, request: object, /) -> object:
        self.requests.append(request)
        return SimpleNamespace(chats=self.chats, messages=self.messages, next_rate=self.next_rate)


@pytest.mark.asyncio
async def test_search_channels_returns_broadcast_channels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient([_channel("cryptonews", title="Crypto", participants_count=900)])
    _patch_client(monkeypatch, client)

    result = await execute_read("acc-1", SearchChannels(query="crypto", limit=10))

    assert isinstance(result, TelegramChannelMatches)
    assert [item.username for item in result.items] == ["cryptonews"]
    assert result.items[0].title == "Crypto"
    assert result.items[0].participants_count == 900
    request = client.requests[0]
    assert isinstance(request, SearchRequest)
    assert request.q == "crypto"
    assert request.limit == 10
    # broadcasts=True is what keeps groups out of the result vector server-side.
    assert request.broadcasts is True


@pytest.mark.asyncio
async def test_search_channels_skips_unlinkable_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    """No handle, not a broadcast, or a non-channel peer — none can join a campaign."""
    client = _FakeClient(
        [
            _channel(None, title="private channel"),
            _channel("   ", title="blank handle"),
            _channel("discussion_group", broadcast=False),
            SimpleNamespace(username="a_user", title="User"),  # no broadcast attribute
            _channel("keeper"),
        ],
    )
    _patch_client(monkeypatch, client)

    result = await execute_read("acc-1", SearchChannels(query="topic"))

    assert isinstance(result, TelegramChannelMatches)
    assert [item.username for item in result.items] == ["keeper"]


@pytest.mark.asyncio
async def test_search_channels_strips_the_handle(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient([_channel("  spaced  ")])
    _patch_client(monkeypatch, client)

    result = await execute_read("acc-1", SearchChannels(query="topic"))

    assert isinstance(result, TelegramChannelMatches)
    assert [item.username for item in result.items] == ["spaced"]


@pytest.mark.asyncio
async def test_short_query_short_circuits_without_an_rpc(monkeypatch: pytest.MonkeyPatch) -> None:
    """Telegram rejects queries under 4 chars, so we do not spend flood budget."""
    client = _FakeClient([_channel("never_seen")])
    _patch_client(monkeypatch, client)

    result = await execute_read("acc-1", SearchChannels(query="abc"))

    assert isinstance(result, TelegramChannelMatches)
    assert result.items == []
    assert client.requests == []


@pytest.mark.asyncio
async def test_whitespace_padded_short_query_also_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient([_channel("never_seen")])
    _patch_client(monkeypatch, client)

    result = await execute_read("acc-1", SearchChannels(query="  ab  "))

    assert isinstance(result, TelegramChannelMatches)
    assert result.items == []
    assert client.requests == []


@pytest.mark.asyncio
async def test_missing_chats_vector_yields_no_items(monkeypatch: pytest.MonkeyPatch) -> None:
    class NoChats:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:
            return SimpleNamespace()

    _patch_client(monkeypatch, NoChats())

    result = await execute_read("acc-1", SearchChannels(query="topic"))

    assert isinstance(result, TelegramChannelMatches)
    assert result.items == []


def _post(post_id: int, channel_id: int, *, date: datetime | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=post_id, peer_id=SimpleNamespace(channel_id=channel_id), date=date)


@pytest.mark.asyncio
async def test_global_post_search_returns_the_posting_channels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two matching posts from one channel are one candidate, not two."""
    client = _FakeClient(
        [_channel("cooking", title="Cooking", channel_id=7)],
        [_post(11, 7), _post(12, 7)],
        next_rate=777,
    )
    _patch_client(monkeypatch, client)

    result = await execute_read("acc-1", SearchGlobalPosts(query="risotto", limit=25))

    # Still a TelegramChannelMatches for every caller that only wants the items.
    assert isinstance(result, TelegramChannelMatches)
    assert isinstance(result, TelegramGlobalPostMatches)
    assert [item.username for item in result.items] == ["cooking"]
    request = client.requests[0]
    assert isinstance(request, SearchGlobalRequest)
    assert request.q == "risotto"
    assert request.limit == 25
    # broadcasts_only is what keeps groups and DMs out of the message index.
    assert request.broadcasts_only is True
    assert isinstance(request.offset_peer, InputPeerEmpty)
    assert (request.offset_rate, request.offset_id) == (0, 0)
    assert result.next_cursor == GlobalPostsCursor(offset_rate=777, peer="cooking", offset_id=12)


@pytest.mark.asyncio
async def test_global_post_search_continues_from_a_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient([_channel("cooking", channel_id=7)], [_post(13, 7)], next_rate=888)
    _patch_client(monkeypatch, client)

    result = await execute_read(
        "acc-1",
        SearchGlobalPosts(
            query="risotto",
            cursor=GlobalPostsCursor(offset_rate=777, peer="cooking", offset_id=12),
        ),
    )

    assert client.resolved == ["cooking"]
    request = client.requests[0]
    assert isinstance(request, SearchGlobalRequest)
    assert (request.offset_rate, request.offset_id) == (777, 12)
    assert request.offset_peer == SimpleNamespace(channel_id=1)
    assert isinstance(result, TelegramGlobalPostMatches)
    assert result.next_cursor == GlobalPostsCursor(offset_rate=888, peer="cooking", offset_id=13)


@pytest.mark.asyncio
async def test_global_post_search_cursor_falls_back_to_the_last_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ``next_rate``: the method documents the last message's date instead.

    That message's channel has no public handle, so the peer offset is dropped —
    the same fallback Telethon's own global-search iterator makes.
    """
    posted_at = datetime(2026, 1, 2, tzinfo=UTC)
    client = _FakeClient([_channel(None, channel_id=7)], [_post(14, 7, date=posted_at)])
    _patch_client(monkeypatch, client)

    result = await execute_read("acc-1", SearchGlobalPosts(query="risotto"))

    assert isinstance(result, TelegramGlobalPostMatches)
    assert result.items == []
    assert result.next_cursor == GlobalPostsCursor(
        offset_rate=int(posted_at.timestamp()),
        peer=None,
        offset_id=14,
    )


@pytest.mark.asyncio
async def test_global_post_search_short_query_pages_until_it_runs_dry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only an EMPTY query is rejected, so 3 chars earn the RPC; no message ends the walk."""
    client = _FakeClient([_channel("abcnews", channel_id=7)], [])
    _patch_client(monkeypatch, client)

    result = await execute_read("acc-1", SearchGlobalPosts(query="abc"))

    assert isinstance(result, TelegramGlobalPostMatches)
    assert [item.username for item in result.items] == ["abcnews"]
    assert len(client.requests) == 1
    assert result.next_cursor is None


@pytest.mark.asyncio
async def test_global_post_search_blank_query_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient([_channel("never_seen", channel_id=7)], [_post(1, 7)])
    _patch_client(monkeypatch, client)

    result = await execute_read("acc-1", SearchGlobalPosts(query="   "))

    assert isinstance(result, TelegramGlobalPostMatches)
    assert result.items == []
    assert result.next_cursor is None
    assert client.requests == []


@pytest.mark.asyncio
async def test_similar_channels_resolves_the_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient([_channel("lookalike")])
    _patch_client(monkeypatch, client)

    result = await execute_read("acc-1", GetSimilarChannels(seed="@durov"))

    assert isinstance(result, TelegramChannelMatches)
    assert [item.username for item in result.items] == ["lookalike"]
    assert client.resolved == ["durov"]
    assert isinstance(client.requests[0], GetChannelRecommendationsRequest)


@pytest.mark.asyncio
async def test_similar_channels_without_a_seed_sends_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient([_channel("recommended")])
    _patch_client(monkeypatch, client)

    result = await execute_read("acc-1", GetSimilarChannels())

    assert isinstance(result, TelegramChannelMatches)
    assert [item.username for item in result.items] == ["recommended"]
    assert client.resolved == []
    request = client.requests[0]
    assert isinstance(request, GetChannelRecommendationsRequest)
    assert request.channel is None


@pytest.mark.asyncio
async def test_similar_channels_blank_seed_is_treated_as_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient([_channel("recommended")])
    _patch_client(monkeypatch, client)

    result = await execute_read("acc-1", GetSimilarChannels(seed="   "))

    assert isinstance(result, TelegramChannelMatches)
    assert client.resolved == []


@pytest.mark.asyncio
async def test_a_seed_telegram_cannot_resolve_is_a_refusal_not_an_empty_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Swallowed, it read as "this seed simply has no recommendations".

    Both spend no RPC and return nothing, so the operator kept a dead handle in the
    form forever. The stable code rides the read ladder like any other refusal.
    """

    class BadSeedClient(_FakeClient):
        async def get_input_entity(self, handle: str, /) -> object:
            msg = f"No user has {handle!r} as username"
            raise ValueError(msg)

    client = BadSeedClient([_channel("never_seen")])
    _patch_client(monkeypatch, client)

    with pytest.raises(TelegramReadError) as excinfo:
        await execute_read("acc-1", GetSimilarChannels(seed="ghost"))

    assert excinfo.value.reason == "channel_not_found"
    assert client.requests == []


@pytest.mark.asyncio
async def test_flood_wait_rides_the_read_error_ladder(monkeypatch: pytest.MonkeyPatch) -> None:
    class FloodingClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:
            raise errors.FloodWaitError(request=MagicMock(), capture=120)

    _patch_client(monkeypatch, FloodingClient())

    with pytest.raises(TelegramReadError) as excinfo:
        await execute_read("acc-1", SearchChannels(query="crypto"))

    assert "FloodWait" in excinfo.value.reason


@pytest.mark.asyncio
async def test_rpc_error_rides_the_read_error_ladder(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingClient(_FakeClient):
        async def __call__(self, _request: object, /) -> object:
            raise errors.RPCError(request=MagicMock(), message="boom", code=400)

    _patch_client(monkeypatch, FailingClient([]))

    with pytest.raises(TelegramReadError):
        await execute_read("acc-1", GetSimilarChannels(seed="durov"))


@pytest.mark.asyncio
async def test_seed_resolution_rpc_failure_rides_the_ladder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only an unresolvable handle is swallowed — an RPC failure must surface."""

    class FloodingResolver(_FakeClient):
        async def get_input_entity(self, _handle: str, /) -> object:
            raise errors.FloodWaitError(request=MagicMock(), capture=60)

    _patch_client(monkeypatch, FloodingResolver([]))

    with pytest.raises(TelegramReadError) as excinfo:
        await execute_read("acc-1", GetSimilarChannels(seed="durov"))

    assert "FloodWait" in excinfo.value.reason


@pytest.mark.asyncio
async def test_linked_group_read_backfills_participants_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The comments probe already pays for getFullChannel — take the count with it."""

    class FullChannelClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:
            return SimpleNamespace(
                full_chat=SimpleNamespace(linked_chat_id=-100999, participants_count=4321),
            )

    _patch_client(monkeypatch, FullChannelClient())

    result = await execute_read("acc-1", GetLinkedDiscussionGroup(channel="@news"))

    assert isinstance(result, LinkedDiscussionGroupResult)
    assert result.comments_enabled is True
    assert result.participants_count == 4321


@pytest.mark.asyncio
async def test_linked_group_read_tolerates_a_missing_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NoCountClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:
            return SimpleNamespace(full_chat=SimpleNamespace(linked_chat_id=None))

    _patch_client(monkeypatch, NoCountClient())

    result = await execute_read("acc-1", GetLinkedDiscussionGroup(channel="@news"))

    assert isinstance(result, LinkedDiscussionGroupResult)
    assert result.comments_enabled is False
    assert result.participants_count is None
