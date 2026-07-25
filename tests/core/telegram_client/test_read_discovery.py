"""Channel-discovery read dispatchers — native Telegram channel search."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from telethon import errors
from telethon.tl.functions.channels import GetChannelRecommendationsRequest
from telethon.tl.functions.contacts import SearchRequest

from core.telegram_client import TelegramReadError, execute_read
from schemas.telegram_actions import (
    GetLinkedDiscussionGroup,
    GetSimilarChannels,
    LinkedDiscussionGroupResult,
    SearchChannels,
)
from schemas.telegram_actions_discovery import TelegramChannelMatches
from tests.core.telegram_client.helpers import patch_read_client as _patch_client


def _channel(
    username: str | None,
    *,
    title: str = "T",
    broadcast: bool = True,
    participants_count: int | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        username=username,
        title=title,
        broadcast=broadcast,
        participants_count=participants_count,
    )


class _FakeClient:
    """Minimal ``client(Request)`` stub that records what it was asked for."""

    def __init__(self, chats: list[object]) -> None:
        self.chats = chats
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
        return SimpleNamespace(chats=self.chats)


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
async def test_unresolvable_seed_yields_no_items_not_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The keyword arm of a sweep must survive a bad seed handle."""

    class BadSeedClient(_FakeClient):
        async def get_input_entity(self, handle: str, /) -> object:
            msg = f"No user has {handle!r} as username"
            raise ValueError(msg)

    client = BadSeedClient([_channel("never_seen")])
    _patch_client(monkeypatch, client)

    result = await execute_read("acc-1", GetSimilarChannels(seed="ghost"))

    assert isinstance(result, TelegramChannelMatches)
    assert result.items == []
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
