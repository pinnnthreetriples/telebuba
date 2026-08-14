"""SSE events endpoint tests — generator behaviour + auth gating + route wiring."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi.responses import StreamingResponse

from api import create_app
from api.v1.events import SESSION_REVOKED_FRAME, event_stream, stream_events
from core import auth as core_auth
from core import events
from core.config import settings
from core.repositories.users import create_user
from schemas.auth import UserRecord
from schemas.logs import LogEntry
from services import auth as auth_service


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
        self.cookies: dict[str, str] = {}

    async def is_disconnected(self) -> bool:
        disconnected = self._calls >= self._connected_calls
        self._calls += 1
        return disconnected


async def _valid(_token: str) -> bool:
    return True


async def _revoked(_token: str) -> bool:
    return False


@pytest.mark.asyncio
async def test_stream_yields_published_entry_as_data_frame() -> None:
    gen = event_stream(
        _FakeRequest(connected_calls=5),  # ty: ignore[invalid-argument-type]
        "token",
        validate_session=_valid,
    )
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
    gen = event_stream(
        _FakeRequest(connected_calls=5),  # ty: ignore[invalid-argument-type]
        "token",
        validate_session=_valid,
    )
    try:
        frame = await asyncio.wait_for(gen.__anext__(), timeout=1)
    finally:
        await gen.aclose()
    assert frame == ": keepalive\n\n"


@pytest.mark.asyncio
async def test_stream_revalidates_once_per_delivery_cycle() -> None:
    validations = 0

    async def _counted_validation(_token: str) -> bool:
        nonlocal validations
        validations += 1
        return True

    gen = event_stream(
        _FakeRequest(connected_calls=5),  # ty: ignore[invalid-argument-type]
        "token",
        validate_session=_counted_validation,
    )
    try:
        for event_name in ("first", "second"):
            pull = asyncio.create_task(gen.__anext__())
            for _ in range(100):
                if events.subscriber_count() == 1:
                    break
                await asyncio.sleep(0)
            events.publish(_entry(event_name))
            assert event_name in await asyncio.wait_for(pull, timeout=1)
    finally:
        await gen.aclose()

    # One initial admission check, then one revalidation immediately before each
    # frame. The previous loop also checked again at the top after every yield.
    assert validations == 3


@pytest.mark.asyncio
async def test_stream_stops_on_disconnect() -> None:
    gen = event_stream(
        _FakeRequest(connected_calls=0),  # ty: ignore[invalid-argument-type]
        "token",
        validate_session=_valid,
    )
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()
    assert events.subscriber_count() == 0


@pytest.mark.asyncio
async def test_stream_closes_after_logout_revokes_its_original_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.auth, "secret", "events-secret-0123456789abcdef-padding")
    await create_user(
        UserRecord(id="stream-user", username="stream-user", password_hash="x", role="admin"),
    )
    token = core_auth.encode_session_token("stream-user")
    gen = event_stream(_FakeRequest(connected_calls=5), token)  # ty: ignore[invalid-argument-type]
    pull = asyncio.create_task(gen.__anext__())
    try:
        for _ in range(100):
            if events.subscriber_count() == 1:
                break
            await asyncio.sleep(0)
        await auth_service.revoke_sessions("stream-user")
        events.publish(_entry("must-not-leak"))
        # The revoked session is told so, and the entry it was waiting for is not
        # delivered — then the stream ends.
        assert await asyncio.wait_for(pull, timeout=1) == SESSION_REVOKED_FRAME
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(gen.__anext__(), timeout=1)
    finally:
        await gen.aclose()
    assert events.subscriber_count() == 0


@pytest.mark.asyncio
async def test_a_stream_whose_session_dies_says_so_instead_of_ending_silently() -> None:
    """A silent close reads as a server restart, and EventSource answers that with a retry.

    The tab this matters for is the one the operator is not watching, so a stream that
    just ends becomes a permanent ~3s poll. The named frame is what lets the client
    close on purpose.
    """
    gen = event_stream(
        _FakeRequest(connected_calls=5),  # ty: ignore[invalid-argument-type]
        "token",
        validate_session=_revoked,
    )
    try:
        assert await asyncio.wait_for(gen.__anext__(), timeout=1) == SESSION_REVOKED_FRAME
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(gen.__anext__(), timeout=1)
    finally:
        await gen.aclose()
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
