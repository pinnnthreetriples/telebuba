"""SSE events endpoint tests — generator behaviour + auth gating + route wiring."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi.responses import StreamingResponse

from api import create_app
from api.v1.events import event_stream, stream_events
from core import events
from core.config import settings
from schemas.logs import LogEntry


def _entry(event: str = "x") -> LogEntry:
    return LogEntry(
        id=7,
        created_at="2026-06-28T00:00:00Z",
        level="INFO",
        status="success",
        account_id=None,
        event=event,
        extra={},
    )


class _FakeRequest:
    """Minimal stand-in: report connected for the first ``connected_calls`` checks."""

    def __init__(self, connected_calls: int) -> None:
        self._calls = 0
        self._connected_calls = connected_calls

    async def is_disconnected(self) -> bool:
        disconnected = self._calls >= self._connected_calls
        self._calls += 1
        return disconnected


@pytest.mark.asyncio
async def test_stream_yields_published_entry_as_data_frame() -> None:
    gen = event_stream(_FakeRequest(connected_calls=5))  # ty: ignore[invalid-argument-type]
    pull = asyncio.ensure_future(gen.__anext__())
    try:
        for _ in range(100):  # wait until the generator has registered its queue
            if events.subscriber_count() == 1:
                break
            await asyncio.sleep(0)
        events.publish(_entry("boom"))
        frame = await asyncio.wait_for(pull, timeout=1)
    finally:
        await gen.aclose()
    assert frame.startswith("data: ")
    assert '"event":"boom"' in frame
    assert events.subscriber_count() == 0  # aclose unsubscribed


@pytest.mark.asyncio
async def test_stream_emits_keepalive_when_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.api, "sse_keepalive_seconds", 0.01)
    gen = event_stream(_FakeRequest(connected_calls=5))  # ty: ignore[invalid-argument-type]
    try:
        frame = await asyncio.wait_for(gen.__anext__(), timeout=1)
    finally:
        await gen.aclose()
    assert frame == ": keepalive\n\n"


@pytest.mark.asyncio
async def test_stream_stops_on_disconnect() -> None:
    gen = event_stream(_FakeRequest(connected_calls=0))  # ty: ignore[invalid-argument-type]
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()
    assert events.subscriber_count() == 0


@pytest.mark.asyncio
async def test_events_requires_auth() -> None:
    application = create_app()  # raw app: the real get_current_user gate runs
    transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/events")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_stream_events_route_returns_event_stream_response() -> None:
    # Call the route directly: assert the streaming response is wired with the
    # SSE media type. (An end-to-end httpx stream over the *infinite* generator
    # deadlocks ASGITransport on close, so the generator itself is tested above.)
    response = await stream_events(_FakeRequest(connected_calls=0))  # ty: ignore[invalid-argument-type]
    assert isinstance(response, StreamingResponse)
    assert response.media_type == "text/event-stream"
