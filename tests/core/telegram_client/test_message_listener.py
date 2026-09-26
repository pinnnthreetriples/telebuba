"""Tests for account-wide inbound message handler ownership and delivery."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from core.telegram_client import _message_listener as listener

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator

    from telethon import TelegramClient


class _PeerUser:
    user_id = 123


class _FakeClient:
    def __init__(self, *, authorized: bool = True) -> None:
        self.authorized = authorized
        self.handlers: list[tuple[object, object]] = []
        self.removed: list[tuple[object, object]] = []
        self.calls: list[str] = []

    async def is_user_authorized(self) -> bool:
        return self.authorized

    def add_event_handler(self, handler: object, event_filter: object) -> None:
        self.calls.append("add")
        self.handlers.append((handler, event_filter))

    def remove_event_handler(self, handler: object, event_filter: object) -> None:
        self.calls.append("remove")
        self.removed.append((handler, event_filter))
        self.handlers = [pair for pair in self.handlers if pair[0] is not handler]

    async def catch_up(self) -> None:
        self.calls.append("catch_up")


@pytest.fixture(autouse=True)
def _reset_listener() -> Iterator[None]:
    listener._reset_for_tests()
    yield
    listener._reset_for_tests()


@pytest.mark.asyncio
async def test_new_inbound_update_maps_peer_and_message_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient()
    monkeypatch.setattr(listener, "get_client", lambda _account_id: _ready(client))
    received = []

    async def record(event: object) -> None:
        received.append(event)

    assert await listener.subscribe_incoming_messages("account", record)
    assert client.calls == ["add", "catch_up"]
    handler = cast("Callable[[object], Awaitable[None]]", client.handlers[0][0])
    await handler(  # type: ignore[operator]
        SimpleNamespace(message=SimpleNamespace(peer_id=_PeerUser(), id=55)),
    )

    assert len(received) == 1
    assert received[0].model_dump() == {
        "account_id": "account",
        "peer_type": "user",
        "peer_id": "123",
        "message_id": 55,
    }


@pytest.mark.asyncio
async def test_pool_rebuild_reattaches_and_stop_fences_queued_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = _FakeClient()
    replacement = _FakeClient()
    monkeypatch.setattr(listener, "get_client", lambda _account_id: _ready(original))
    received = []

    async def record(event: object) -> None:
        received.append(event)

    assert await listener.subscribe_incoming_messages("account", record)
    stale_handler = cast("Callable[[object], Awaitable[None]]", original.handlers[0][0])

    await listener._reattach_on_rebuild("account", cast("TelegramClient", replacement))
    assert len(replacement.handlers) == 1
    assert replacement.calls == ["add", "catch_up"]
    await listener.stop_incoming_messages("account")
    await stale_handler(
        SimpleNamespace(message=SimpleNamespace(peer_id=_PeerUser(), id=56)),
    )

    assert replacement.removed
    assert received == []


@pytest.mark.asyncio
async def test_unauthorized_client_does_not_register_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(authorized=False)
    monkeypatch.setattr(listener, "get_client", lambda _account_id: _ready(client))

    assert not await listener.subscribe_incoming_messages("account", _noop)
    assert client.handlers == []


async def _ready(client: _FakeClient) -> _FakeClient:
    return client


async def _noop(_event: object) -> None:
    return None
