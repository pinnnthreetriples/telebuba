"""Bounded inbox-event fan-out tests."""

from __future__ import annotations

import asyncio

import pytest

from core import inbox_events
from schemas.inbox_events import InboxMessageReceived


def _event(message_id: int = 1) -> InboxMessageReceived:
    return InboxMessageReceived(
        account_id="account",
        peer_type="user",
        peer_id="123",
        message_id=message_id,
    )


@pytest.mark.asyncio
async def test_event_reaches_each_subscriber() -> None:
    async with inbox_events.subscribe() as left, inbox_events.subscribe() as right:
        inbox_events.publish(_event())
        assert await asyncio.wait_for(left.get(), timeout=1) == _event()
        assert await asyncio.wait_for(right.get(), timeout=1) == _event()


@pytest.mark.asyncio
async def test_full_subscriber_queue_drops_only_that_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(inbox_events.settings.api, "sse_max_queue", 1)
    async with inbox_events.subscribe() as slow, inbox_events.subscribe() as healthy:
        inbox_events.publish(_event(1))
        assert await healthy.get() == _event(1)
        inbox_events.publish(_event(2))
        assert await slow.get() == _event(1)
        assert await healthy.get() == _event(2)
