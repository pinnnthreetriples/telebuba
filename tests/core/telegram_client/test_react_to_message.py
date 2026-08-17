"""Reacting to ONE named message: whitelist-first, and a skip is never a failure."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from telethon import errors
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.messages import SendReactionRequest
from telethon.tl.types import ChatReactionsAll, ChatReactionsNone, ChatReactionsSome, ReactionEmoji

from core.telegram_client import execute, invalidate_reaction_whitelist_cache
from schemas.telegram_actions import ReactToMessage
from tests.core.telegram_client.helpers import patch_action_client as _patch_client

if TYPE_CHECKING:
    from collections.abc import Iterator

_ACTION = ReactToMessage(chat_id=777, message_id=31, emoji="🔥")


@pytest.fixture(autouse=True)
def _clean_whitelist_cache() -> Iterator[None]:
    """The whitelist cache is process-global, so a leftover entry would order-couple tests."""
    invalidate_reaction_whitelist_cache()
    yield
    invalidate_reaction_whitelist_cache()


class _ReactClient:
    def __init__(self, available: object, *, send_error: Exception | None = None) -> None:
        self.available = available
        self.send_error = send_error
        self.requests: list[object] = []
        self.peers: list[object] = []

    async def connect(self) -> None:
        return None

    async def get_input_entity(self, peer: object) -> object:
        self.peers.append(peer)
        return f"input:{peer}"

    async def __call__(self, request: object) -> object:
        self.requests.append(request)
        if isinstance(request, GetFullChannelRequest):
            return MagicMock(full_chat=MagicMock(available_reactions=self.available))
        if self.send_error is not None:
            raise self.send_error
        return MagicMock()


def _sent(client: _ReactClient) -> list[SendReactionRequest]:
    return [r for r in client.requests if isinstance(r, SendReactionRequest)]


@pytest.mark.asyncio
async def test_the_operators_emoji_lands_when_the_chat_permits_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _ReactClient(ChatReactionsSome(reactions=[ReactionEmoji(emoticon="🔥")]))
    _patch_client(monkeypatch, client)

    result = await execute("acc-1", _ACTION)

    assert (result.status, result.message_id) == ("ok", 31)
    placed = [
        (r.msg_id, r.reaction[0].emoticon)  # ty: ignore[not-subscriptable, unresolved-attribute]
        for r in _sent(client)
    ]
    assert placed == [(31, "🔥")]
    # The int goes down as an int: the chat id is a session-cache key, and the same
    # digits as a string would be sent to the username resolver instead.
    assert client.peers == [777]


@pytest.mark.asyncio
async def test_a_reaction_outside_the_channel_whitelist_skips_the_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The operator chose this emoji, so a substitute would publish words they did not pick.

    Skipping is also the right accounting: a chat that restricts reactions has not
    failed the campaign, and treating it as a failure would spend a reserve account
    on a chat setting.
    """
    client = _ReactClient(ChatReactionsSome(reactions=[ReactionEmoji(emoticon="👍")]))
    _patch_client(monkeypatch, client)

    result = await execute("acc-1", _ACTION)

    assert (result.status, result.message_id) == ("ok", None)
    assert _sent(client) == []


@pytest.mark.asyncio
async def test_a_chat_with_reactions_switched_off_skips_rather_than_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _ReactClient(ChatReactionsNone())
    _patch_client(monkeypatch, client)

    result = await execute("acc-1", _ACTION)

    assert result.status == "ok"
    assert _sent(client) == []


@pytest.mark.asyncio
async def test_an_unrestricted_chat_is_not_narrowed(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _ReactClient(ChatReactionsAll())
    _patch_client(monkeypatch, client)

    result = await execute("acc-1", _ACTION)

    assert result.message_id == 31
    assert len(_sent(client)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "refusal",
    [
        errors.ReactionInvalidError(request=None),
        errors.ReactionsTooManyError(request=None),
    ],
)
async def test_a_late_reaction_refusal_is_still_a_skip(
    monkeypatch: pytest.MonkeyPatch,
    refusal: Exception,
) -> None:
    """The whitelist can be stale, unreadable, or silent about per-account limits.

    A non-Premium account may hold exactly one reaction per message, so the same
    verdict can arrive from the server after the check passed.
    """
    client = _ReactClient(ChatReactionsAll(), send_error=refusal)
    _patch_client(monkeypatch, client)

    result = await execute("acc-1", _ACTION)

    assert (result.status, result.message_id) == ("ok", None)


@pytest.mark.asyncio
async def test_a_whitelist_read_that_fails_does_not_block_the_reaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreadable availability is "unknown", not "forbidden".

    The dispatcher still tries, and the server's own refusal (above) is what turns it
    into a skip — otherwise a transient read error would silence every reaction.
    """

    class _Failing(_ReactClient):
        async def __call__(self, request: object) -> object:
            if isinstance(request, GetFullChannelRequest):
                raise errors.ChannelPrivateError(request=None)
            return await super().__call__(request)

    client = _Failing(ChatReactionsAll())
    _patch_client(monkeypatch, client)

    result = await execute("acc-1", _ACTION)

    assert result.message_id == 31


@pytest.mark.asyncio
async def test_a_settings_change_mid_flight_abandons_the_reaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful channel-settings write while we were resolving invalidates the verdict."""

    class _Racing(_ReactClient):
        async def get_input_entity(self, peer: object) -> object:
            invalidate_reaction_whitelist_cache()
            return await super().get_input_entity(peer)

    client = _Racing(ChatReactionsAll())
    _patch_client(monkeypatch, client)

    result = await execute("acc-1", _ACTION)

    assert (result.status, result.message_id) == ("ok", None)
    assert _sent(client) == []
