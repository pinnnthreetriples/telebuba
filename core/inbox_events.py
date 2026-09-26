"""Bounded in-process fan-out for typed inbox notifications."""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from core.config import settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from schemas.inbox_events import InboxMessageReceived

_subscribers: set[asyncio.Queue[InboxMessageReceived]] = set()


def publish(event: InboxMessageReceived) -> None:
    """Deliver a message nudge to each live subscriber without blocking Telegram updates."""
    for queue in _subscribers:
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(event)


@asynccontextmanager
async def subscribe() -> AsyncIterator[asyncio.Queue[InboxMessageReceived]]:
    """Register one bounded queue for the lifetime of an SSE request."""
    queue: asyncio.Queue[InboxMessageReceived] = asyncio.Queue(
        maxsize=settings.api.sse_max_queue,
    )
    _subscribers.add(queue)
    try:
        yield queue
    finally:
        _subscribers.discard(queue)


def subscriber_count() -> int:
    """Number of live inbox SSE subscribers (diagnostics + tests)."""
    return len(_subscribers)
