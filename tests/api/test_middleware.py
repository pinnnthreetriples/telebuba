"""The two ASGI wrappers: the request-body ceiling and the security headers.

The body ceiling is tested with STREAMED input on purpose. An earlier attempt at
this guard only inspected ``Content-Length``, and a probe walked straight past it
with ``Transfer-Encoding: chunked``: 3,145,728 bytes reached the server through a
path whose in-handler guard had only ever observed the final part size. Handing
httpx an async iterator reproduces that exactly — it sends no ``Content-Length``
at all, and the iterator advances only when the app calls ``receive()``, so the
tally below is the number of bytes the server genuinely pulled off the wire.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from api import create_app
from api._middleware import (
    ASGIApp,
    BodySizeLimitMiddleware,
    Message,
    Receive,
    Scope,
    SecurityHeadersMiddleware,
    Send,
)
from core.config import settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from fastapi import FastAPI

_BOUNDARY = "----telebubatest"
_CONTENT_TYPE = f"multipart/form-data; boundary={_BOUNDARY}"
_CHUNK = b"z" * 65_536
_CAP = 1_000_000
_SECURITY_HEADER_NAMES = (
    "x-content-type-options",
    "x-frame-options",
    "content-security-policy",
    "referrer-policy",
    "cross-origin-opener-policy",
)


class _StreamedUpload:
    """A chunked multipart body that records how much of itself was consumed."""

    def __init__(self, chunks: int) -> None:
        self._chunks = chunks
        self.sent = 0
        self.finished = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        head = (
            f"--{_BOUNDARY}\r\n"
            'Content-Disposition: form-data; name="file"; filename="a.session"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        self.sent += len(head)
        yield head
        for _ in range(self._chunks):
            self.sent += len(_CHUNK)
            yield _CHUNK
        tail = f"\r\n--{_BOUNDARY}--\r\n".encode()
        self.sent += len(tail)
        yield tail
        self.finished = True


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def capped(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """A raw app (real auth dependency) with a small body ceiling."""
    monkeypatch.setattr(settings.api, "max_request_bytes", _CAP)
    return create_app()


@pytest.mark.asyncio
async def test_chunked_body_past_the_cap_is_refused_and_cut_off(capped: FastAPI) -> None:
    body = _StreamedUpload(chunks=48)  # 3_145_728 bytes, ~3x the cap
    async with _client(capped) as client:
        resp = await client.post(
            "/api/v1/accounts/import-session",
            content=body,
            headers={"Content-Type": _CONTENT_TYPE},
        )

    # The transfer declared no length, so nothing could have been bounded up front.
    assert resp.request.headers.get("transfer-encoding") == "chunked"
    assert "content-length" not in resp.request.headers
    assert resp.status_code == 413
    assert resp.json() == {
        "error": {"code": "payload_too_large", "message": "payload_too_large"},
    }
    # The stream was cut off, not drained: the server can only stop after the chunk
    # that crosses the cap, so one chunk of overshoot is the whole exposure.
    assert not body.finished
    assert body.sent <= _CAP + len(_CHUNK)


@pytest.mark.asyncio
async def test_the_ceiling_answers_before_authentication(capped: FastAPI) -> None:
    """413, not 401: the caller must be stopped before the auth dependency resolves.

    A 401 here would mean the body had already been parsed and spooled — the whole
    defect. ``capped`` deliberately has no ``get_current_user`` override, so the
    real session gate is in play and would answer 401 if it were reached first.
    """
    body = _StreamedUpload(chunks=48)
    async with _client(capped) as client:
        resp = await client.post(
            "/api/v1/accounts/import-session",
            content=body,
            headers={"Content-Type": _CONTENT_TYPE},
        )
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_declared_oversize_body_is_refused_too(capped: FastAPI) -> None:
    """The same counter bounds an honestly-declared ``Content-Length``."""
    async with _client(capped) as client:
        resp = await client.post(
            "/api/v1/accounts/import-session",
            files={"file": ("a.session", b"y" * (_CAP + 1), "application/octet-stream")},
        )
    assert resp.request.headers["content-length"] is not None
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_a_streamed_body_under_the_cap_still_reaches_the_route(app: FastAPI) -> None:
    """Regression guard: the wrapper must not break normal streamed uploads."""
    body = _StreamedUpload(chunks=1)  # 65_536 bytes, far under the real default
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/import-session",
            content=body,
            headers={"Content-Type": _CONTENT_TYPE},
        )
    assert resp.status_code == 200
    assert resp.json()["account_id"] == "a"
    assert body.finished
    assert (settings.telegram.session_dir / "a.session").read_bytes() == _CHUNK


@pytest.mark.asyncio
async def test_a_get_with_no_body_is_untouched(capped: FastAPI) -> None:
    async with _client(capped) as client:
        resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_every_response_carries_the_security_headers(app: FastAPI) -> None:
    async with _client(app) as client:
        ok = await client.get("/api/v1/health")
        missing = await client.get("/api/v1/nope")
    for resp in (ok, missing):
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["x-frame-options"] == "DENY"
        assert resp.headers["content-security-policy"] == "frame-ancestors 'none'"
        assert resp.headers["referrer-policy"] == "no-referrer"
        assert resp.headers["cross-origin-opener-policy"] == "same-origin"


@pytest.mark.asyncio
async def test_the_refusal_is_hardened_too(capped: FastAPI) -> None:
    """The 413 is produced outside the routers, so it needs the headers explicitly."""
    body = _StreamedUpload(chunks=48)
    async with _client(capped) as client:
        resp = await client.post(
            "/api/v1/accounts/import-session",
            content=body,
            headers={"Content-Type": _CONTENT_TYPE},
        )
    assert resp.status_code == 413
    for name in _SECURITY_HEADER_NAMES:
        assert name in resp.headers


async def _idle_receive() -> Message:
    return {"type": "http.request", "body": b""}


async def _discard(_message: Message) -> None:
    return None


@pytest.mark.asyncio
async def test_a_response_that_set_its_own_policy_keeps_it() -> None:
    """Never overwrite: a route that chose a header meant it."""
    sent: list[Message] = []

    async def _app(_scope: Scope, _receive: Receive, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"referrer-policy", b"origin")],
            },
        )

    async def _capture(message: Message) -> None:
        sent.append(message)

    await SecurityHeadersMiddleware(_app)({"type": "http"}, _idle_receive, _capture)
    headers = dict(sent[0]["headers"])
    assert headers[b"referrer-policy"] == b"origin"
    assert headers[b"x-content-type-options"] == b"nosniff"


@pytest.mark.parametrize(
    "wrap",
    [SecurityHeadersMiddleware, lambda app: BodySizeLimitMiddleware(app, max_bytes=1)],
)
@pytest.mark.asyncio
async def test_non_http_scopes_pass_straight_through(wrap: Callable[[ASGIApp], ASGIApp]) -> None:
    """Lifespan and websocket traffic has no headers to stamp and no body to count."""
    seen: list[str] = []

    async def _app(scope: Scope, _receive: Receive, _send: Send) -> None:
        seen.append(scope["type"])

    await wrap(_app)({"type": "lifespan"}, _idle_receive, _discard)
    assert seen == ["lifespan"]
