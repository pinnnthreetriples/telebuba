"""Subscription ownership races and bounded history read tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from core.telegram_client import _listener

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from schemas.telegram_actions import NewPostEvent


class _Client:
    def __init__(self) -> None:
        self.handlers: list[tuple[object, object]] = []

    async def get_peer_id(self, _channel: str) -> int:
        return -100

    def add_event_handler(self, handler: object, event_filter: object) -> None:
        self.handlers.append((handler, event_filter))

    def remove_event_handler(self, handler: object, _event_filter: object) -> None:
        self.handlers = [item for item in self.handlers if item[0] is not handler]


@pytest.fixture(autouse=True)
def _reset() -> None:
    _listener._reset_for_tests()
    _listener._CLIENTS.clear()


@pytest.mark.asyncio
async def test_stop_wins_against_subscribe_stalled_while_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client()
    started, release = asyncio.Event(), asyncio.Event()

    async def _get(_account_id: str) -> _Client:
        started.set()
        await release.wait()
        _listener._CLIENTS["acc"] = client  # ty: ignore[invalid-assignment]
        return client

    monkeypatch.setattr(_listener, "get_client", _get)
    subscribe = asyncio.create_task(_listener.subscribe_posts("acc", ["@news"], _noop))
    await started.wait()
    await _listener.stop_post_listener("acc")
    release.set()

    assert await subscribe == []
    assert client.handlers == []


@pytest.mark.asyncio
async def test_stop_does_not_wait_for_slow_peer_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client()
    entered, release = asyncio.Event(), asyncio.Event()

    async def _slow_peer(_channel: str) -> int:
        entered.set()
        await release.wait()
        return -100

    async def _get(_account_id: str) -> _Client:
        _listener._CLIENTS["acc"] = client  # ty: ignore[invalid-assignment]
        return client

    monkeypatch.setattr(client, "get_peer_id", _slow_peer)
    monkeypatch.setattr(_listener, "get_client", _get)
    subscribe = asyncio.create_task(_listener.subscribe_posts("acc", ["@news"], _noop))
    await entered.wait()

    await asyncio.wait_for(_listener.stop_post_listener("acc"), timeout=0.1)
    release.set()
    assert await subscribe == []
    assert client.handlers == []


@pytest.mark.asyncio
async def test_stale_handler_generation_drops_event_after_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client()

    async def _get(_account_id: str) -> _Client:
        _listener._CLIENTS["acc"] = client  # ty: ignore[invalid-assignment]
        return client

    monkeypatch.setattr(_listener, "get_client", _get)
    received: list[int] = []

    async def _receive(event: NewPostEvent) -> None:
        received.append(event.post_id)

    await _listener.subscribe_posts("acc", ["@news"], _receive)
    old_handler = client.handlers[0][0]
    await _listener.subscribe_posts("acc", ["@news"], _receive)
    event = SimpleNamespace(
        chat_id=-100,
        message=SimpleNamespace(
            id=1,
            message="x",
            media=None,
            post=True,
            fwd_from=None,
            grouped_id=None,
            date=datetime.now(UTC),
        ),
    )
    handler = cast("Callable[[object], Awaitable[None]]", old_handler)
    await handler(event)
    assert received == []


async def _noop(_event: object) -> None:
    return None
