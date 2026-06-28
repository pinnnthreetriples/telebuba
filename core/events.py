"""In-process pub/sub for live runtime/log events — the SSE backbone.

Every :func:`core.logging.log_event` publishes the persisted ``LogEntry`` here;
each SSE subscriber holds its own bounded queue. Single-worker uvicorn (per the
split ADR) makes an in-process fan-out sufficient — no external broker.
"""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from core.config import settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from schemas.logs import LogEntry

_subscribers: set[asyncio.Queue[LogEntry]] = set()


def publish(entry: LogEntry) -> None:
    """Fan ``entry`` out to every live subscriber — non-blocking, best-effort.

    A full queue means a slow consumer; drop that frame for it rather than block
    the logging hot path (the FE's fallback poll re-syncs the dropped row).
    """
    for queue in _subscribers:
        # ponytail: drop the frame on a slow consumer (full queue) rather than
        # block the logging hot path; the FE's fallback poll re-syncs the row.
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(entry)


@asynccontextmanager
async def subscribe() -> AsyncIterator[asyncio.Queue[LogEntry]]:
    """Register a bounded subscriber queue for the lifetime of the context."""
    queue: asyncio.Queue[LogEntry] = asyncio.Queue(maxsize=settings.api.sse_max_queue)
    _subscribers.add(queue)
    try:
        yield queue
    finally:
        _subscribers.discard(queue)


def subscriber_count() -> int:
    """Number of live subscribers (diagnostics + tests)."""
    return len(_subscribers)
