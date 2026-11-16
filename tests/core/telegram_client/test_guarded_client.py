"""A request on our client never ends in Telethon's own cancellation; a real cancel still does."""

from __future__ import annotations

import asyncio

import pytest
from telethon import TelegramClient, errors
from telethon.sessions import MemorySession

from core.telegram_client._guarded_client import (
    GuardedTelegramClient,
    TelegramClientDisconnectedError,
)


def _client(monkeypatch: pytest.MonkeyPatch, answer: object) -> GuardedTelegramClient:
    async def _call(_self: object, _sender: object, _request: object, **_kwargs: object) -> object:
        if isinstance(answer, BaseException):
            raise answer
        if answer == "hang":
            await asyncio.Event().wait()
        return answer

    monkeypatch.setattr(TelegramClient, "_call", _call)
    return GuardedTelegramClient(MemorySession(), 1, "hash")


@pytest.mark.asyncio
async def test_an_answer_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, "pong")

    assert await client._call(None, object()) == "pong"


@pytest.mark.asyncio
async def test_a_request_cancelled_by_telethons_disconnect_is_a_lost_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # What ``MTProtoSender._disconnect()`` does to a pending request's future.
    client = _client(monkeypatch, asyncio.CancelledError())

    with pytest.raises(TelegramClientDisconnectedError):
        await client._call(None, object())


@pytest.mark.asyncio
async def test_a_disconnect_caused_by_a_dead_key_raises_the_dead_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Telethon's update loop records why it disconnected before cancelling the requests.
    client = _client(monkeypatch, asyncio.CancelledError())
    dead = errors.AuthKeyUnregisteredError(request=None)
    client._updates_error = dead

    with pytest.raises(errors.AuthKeyUnregisteredError) as raised:
        await client._call(None, object())

    assert raised.value is dead


@pytest.mark.asyncio
async def test_cancelling_the_callers_own_task_still_cancels_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, "hang")
    task = asyncio.create_task(client._call(None, object()))
    await asyncio.sleep(0)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_a_deadline_around_the_request_is_still_a_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, "hang")

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.01):
            await client._call(None, object())
