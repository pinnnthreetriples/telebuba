"""Unit tests for the channel-liveness read (``_read_activity.py``).

Small surface, but the whole inactive-channel rule reads its verdict off this one value,
so the shapes that could quietly become "no posts" are pinned here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from core.telegram_client._read_activity import dispatch_get_last_post_at
from schemas.telegram_actions_activity import GetLastPostAt

if TYPE_CHECKING:
    from telethon import TelegramClient


class _FakeClient:
    """Records the request and returns a canned page of messages."""

    def __init__(self, messages: list[object]) -> None:
        self.messages = messages
        self.calls: list[tuple[object, int]] = []

    async def get_messages(self, entity: object, limit: int) -> list[object]:
        self.calls.append((entity, limit))
        return self.messages


def _as_client(fake: _FakeClient) -> TelegramClient:
    """The dispatcher only ever calls ``get_messages``; the stub supplies exactly that."""
    return cast("TelegramClient", fake)


@pytest.mark.asyncio
async def test_returns_the_newest_message_date_in_utc() -> None:
    """A non-UTC date must be normalised, or a string compare would put it in the wrong week."""
    moscow = datetime(2026, 8, 11, 15, 30, tzinfo=timezone(timedelta(hours=3)))
    client = _FakeClient([SimpleNamespace(date=moscow)])

    result = await dispatch_get_last_post_at(_as_client(client), GetLastPostAt(channel="@news"))

    assert result.last_post_at == datetime(2026, 8, 11, 12, 30, tzinfo=UTC).isoformat()
    # One message and no paging: the caller compares against a single cutoff.
    assert client.calls == [("@news", 1)]


@pytest.mark.asyncio
async def test_an_empty_channel_reads_as_none() -> None:
    action = GetLastPostAt(channel="@news")

    result = await dispatch_get_last_post_at(_as_client(_FakeClient([])), action)

    assert result.last_post_at is None


@pytest.mark.asyncio
async def test_a_message_without_a_date_reads_as_none_rather_than_raising() -> None:
    """Telethon always sets one; the guard is for a stub or a truncated update."""
    client = _FakeClient([SimpleNamespace()])

    result = await dispatch_get_last_post_at(_as_client(client), GetLastPostAt(channel="@news"))

    assert result.last_post_at is None
