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
from services import auth as auth_service
from services.events import subscribe

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable

router = APIRouter(tags=["events"])

# A named terminal frame, sent once before the generator returns on a dead session.
#
# Closing the generator silently is indistinguishable, to a native ``EventSource``,
# from the server going away — and its answer to that is to reconnect in ~3s,
# forever. The tab that most needs closing (a second one the operator forgot, a
# stolen cookie) is exactly the one that never stops asking, so revocation turned
# into a permanent poll, each attempt costing a session lookup. Saying *why* the
# stream ended lets the client close deliberately instead of retrying.
SESSION_REVOKED_FRAME = "event: session-invalid\ndata: {}\n\n"


async def _session_is_valid(token: str) -> bool:
    return bool(token and await auth_service.resolve_user(token) is not None)


async def event_stream(
    request: Request,
    session_token: str,
    *,
    validate_session: Callable[[str], Awaitable[bool]] = _session_is_valid,
) -> AsyncGenerator[str]:
    """Yield each live ``LogEntry`` as an SSE ``data:`` frame until disconnect.

    A keepalive comment is emitted whenever no event arrives within the
    configured window, so idle proxies don't close the stream.

    Every exit caused by a dead session emits ``SESSION_REVOKED_FRAME`` first, so
    the client can tell revocation from a server restart and stop reconnecting.
    """
    async with subscribe() as queue:
        if not await validate_session(session_token):
            yield SESSION_REVOKED_FRAME
            return
        while not await request.is_disconnected():
            try:
                entry = await asyncio.wait_for(
                    queue.get(),
                    timeout=settings.api.sse_keepalive_seconds,
                )
            except TimeoutError:
                if not await validate_session(session_token):
                    yield SESSION_REVOKED_FRAME
                    return
                yield ": keepalive\n\n"
                continue
            if not await validate_session(session_token):
                yield SESSION_REVOKED_FRAME
                return
            yield f"data: {entry.model_dump_json()}\n\n"


@router.get("/events", include_in_schema=False)
async def stream_events(request: Request) -> StreamingResponse:
    # The protected-router dependency validated this same cookie before the
    # response started. Pass the original token into the long-lived generator so
    # expiry or a logout token-version bump closes the already-open stream too.
    token = request.cookies.get(settings.auth.cookie_name, "")
    return StreamingResponse(event_stream(request, token), media_type="text/event-stream")
