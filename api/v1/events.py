"""SSE endpoint — streams live ``LogEntry`` events over ``text/event-stream``.

The FE consumes this with a native ``EventSource`` (cookie auth, same-origin).
The generated client can't model a stream, so the route is hidden from OpenAPI
(``include_in_schema=False``) — the payload type ``LogEntry`` is already in the
generated client via ``GET /logs``, and hiding it keeps gen-api drift at zero.
Auth is enforced by the protected-router dependency in ``api.v1.__init__``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from core.config import settings
from services.events import subscribe

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

router = APIRouter(tags=["events"])


async def event_stream(request: Request) -> AsyncGenerator[str]:
    """Yield each live ``LogEntry`` as an SSE ``data:`` frame until disconnect.

    A keepalive comment is emitted whenever no event arrives within the
    configured window, so idle proxies don't close the stream.
    """
    async with subscribe() as queue:
        while not await request.is_disconnected():
            try:
                entry = await asyncio.wait_for(
                    queue.get(),
                    timeout=settings.api.sse_keepalive_seconds,
                )
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            yield f"data: {entry.model_dump_json()}\n\n"


@router.get("/events", include_in_schema=False)
async def stream_events(request: Request) -> StreamingResponse:
    return StreamingResponse(event_stream(request), media_type="text/event-stream")
