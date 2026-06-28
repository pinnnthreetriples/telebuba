"""Tests for the in-process live-event bus (``core.events``)."""

from __future__ import annotations

import asyncio

import pytest

from core import events
from schemas.logs import LogEntry


def _entry(event: str = "x") -> LogEntry:
    return LogEntry(
        id=1,
        created_at="2026-06-28T00:00:00Z",
        level="INFO",
        status="success",
        account_id=None,
        event=event,
        extra={},
    )


@pytest.mark.asyncio
async def test_subscribe_receives_published_entry() -> None:
    async with events.subscribe() as queue:
        events.publish(_entry("hello"))
        received = await asyncio.wait_for(queue.get(), timeout=1)
    assert received.event == "hello"


@pytest.mark.asyncio
async def test_publish_with_no_subscribers_is_noop() -> None:
    events.publish(_entry())  # must not raise
    assert events.subscriber_count() == 0


@pytest.mark.asyncio
async def test_subscribe_registers_and_unregisters() -> None:
    assert events.subscriber_count() == 0
    async with events.subscribe():
        assert events.subscriber_count() == 1
    assert events.subscriber_count() == 0


@pytest.mark.asyncio
async def test_full_queue_drops_frame_without_blocking(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(events.settings.api, "sse_max_queue", 1)
    async with events.subscribe() as queue:
        events.publish(_entry("a"))  # fills the maxsize-1 queue
        events.publish(_entry("b"))  # dropped — must not raise or block
        first = await asyncio.wait_for(queue.get(), timeout=1)
        assert queue.empty()
    assert first.event == "a"
