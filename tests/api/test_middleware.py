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


def _multipart_of_exactly(total: int) -> bytes:
    """One multipart body whose WHOLE length is ``total`` — what the counter tallies."""
    head = (
        f"--{_BOUNDARY}\r\n"
        'Content-Disposition: form-data; name="file"; filename="a.session"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode()
    tail = f"\r\n--{_BOUNDARY}--\r\n".encode()
    return head + b"p" * (total - len(head) - len(tail)) + tail


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def capped(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """A raw app (real auth dependency) with both ceilings pulled down to ``_CAP``.

    For the cases about the counter's own mechanics, where a 210 MB body would be
    absurd to stream. The cases about what SHIPS take no fixture — see
    ``test_the_reported_case_is_cut_off_at_the_shipped_default``.
    """
    monkeypatch.setattr(settings.api, "max_request_bytes", _CAP)
    monkeypatch.setattr(settings.api, "max_anonymous_request_bytes", _CAP)
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
    # The stream was cut off, not drained. The middleware can only reject after the
    # `http.request` message that crosses the cap, so the overshoot is one message —
    # and what bounds a MESSAGE is uvicorn, not this middleware: `h11_impl` stops
    # reading once `flow_control.HIGH_WATER_LIMIT` (65536) is buffered. This test
    # feeds 64 KiB chunks itself, so it pins the middleware's half of that contract
    # (reject on the first crossing message) and not the transport's.
    assert not body.finished
    assert body.sent <= _CAP + len(_CHUNK)


@pytest.mark.asyncio
async def test_the_reported_case_is_cut_off_at_the_shipped_default() -> None:
    """No fixture, no monkeypatch: the real 210 MB / 1 MB config, as deployed.

    This is the test the first attempt was missing. Both proofs ran under `capped`,
    which tightens the ceiling 210x, so nothing exercised what ships — and at the
    shipped default the reported 3.1 MB is 1.5% of the ceiling, so the middleware
    never engaged and the original probe still drained in full behind a 401.
    """
    assert settings.api.max_request_bytes == 210_000_000
    body = _StreamedUpload(chunks=48)  # 3_145_728 bytes: the reported case
    async with _client(create_app()) as client:
        resp = await client.post(
            "/api/v1/accounts/import-session",
            content=body,
            headers={"Content-Type": _CONTENT_TYPE},
        )
    assert resp.request.headers.get("transfer-encoding") == "chunked"
    assert resp.status_code == 413
    assert not body.finished
    assert body.sent <= settings.api.max_anonymous_request_bytes + len(_CHUNK)


@pytest.mark.asyncio
async def test_a_session_cookie_buys_the_upload_budget() -> None:
    """The split is on cookie PRESENCE, so a real operator's upload still works.

    The cookie here is forged, and that is the point: the middleware does not
    validate it, so this request gets the 210 MB budget and is then refused by the
    auth dependency on its merits (401, body drained). A forger buys back only the
    budget they already had before this change — no reduction is lost, because the
    caller who sends NO cookie is the one now held to 1 MB.
    """
    body = _StreamedUpload(chunks=48)
    async with _client(create_app()) as client:
        resp = await client.post(
            "/api/v1/accounts/import-session",
            content=body,
            headers={"Content-Type": _CONTENT_TYPE, "Cookie": "tb_session=forged"},
        )
    assert resp.status_code == 401
    assert body.finished


@pytest.mark.asyncio
async def test_a_lookalike_cookie_name_does_not_buy_the_budget() -> None:
    """``xtb_session`` must not pass as ``tb_session``: compare the key, not a substring."""
    body = _StreamedUpload(chunks=48)
    async with _client(create_app()) as client:
        resp = await client.post(
            "/api/v1/accounts/import-session",
            content=body,
            headers={"Content-Type": _CONTENT_TYPE, "Cookie": "xtb_session=forged; other=1"},
        )
    assert resp.status_code == 413
    assert not body.finished


@pytest.mark.asyncio
async def test_the_cookie_is_found_among_others(app: FastAPI) -> None:
    """A browser sends the session cookie alongside whatever else is set for the host."""
    body = _StreamedUpload(chunks=1)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/import-session",
            content=body,
            headers={
                "Content-Type": _CONTENT_TYPE,
                "Cookie": "theme=dark; tb_session=forged; lang=ru",
            },
        )
    assert resp.status_code == 200
    assert body.finished


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


@pytest.mark.parametrize(("body_bytes", "status"), [(_CAP, 401), (_CAP + 1, 413)])
@pytest.mark.asyncio
async def test_the_limit_itself_is_allowed_and_one_byte_over_is_not(
    monkeypatch: pytest.MonkeyPatch,
    body_bytes: int,
    status: int,
) -> None:
    """``max_bytes`` is inclusive: exactly the limit passes, one more does not.

    Nothing pinned this, so `>` and `>=` in ``_BodyCounter.receive`` were
    indistinguishable — the whole of tests/api passed either way. Swapping the
    comparison would start refusing a body of exactly the configured size, which is
    the one value an operator sizing a limit will actually send. The 401 is the
    budget being GRANTED and the forged cookie then failing auth on its merits;
    a 413 there would mean the counter had refused the limit itself.
    """
    monkeypatch.setattr(settings.api, "max_request_bytes", _CAP)
    monkeypatch.setattr(settings.api, "max_anonymous_request_bytes", _CAP)
    body = _multipart_of_exactly(body_bytes)
    assert len(body) == body_bytes

    async with _client(create_app()) as client:
        resp = await client.post(
            "/api/v1/accounts/import-session",
            content=body,
            headers={"Content-Type": _CONTENT_TYPE, "Cookie": "tb_session=forged"},
        )
    assert resp.status_code == status


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


@pytest.mark.asyncio
async def test_a_started_response_is_never_followed_by_a_413() -> None:
    """Two ``http.response.start`` messages on one request is a protocol error.

    Latent: it needs a route that starts streaming and only THEN reads past the cap,
    and none does. But if one ever did, the caller would get a 200 start, a body
    chunk, and then a 413 start on the same request — uvicorn rejects that, so a
    path meant to answer 413 would answer 500 instead.
    """
    sent: list[Message] = []
    pending = [
        {"type": "http.request", "body": b"x" * 100, "more_body": True},
        {"type": "http.request", "body": b"x" * 100, "more_body": True},
    ]

    async def _receive() -> Message:
        return pending.pop(0)

    async def _app(_scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"partial", "more_body": True})
        while True:  # reads the body late, and runs past the cap doing it
            await receive()

    async def _capture(message: Message) -> None:
        sent.append(message)

    wrapped = BodySizeLimitMiddleware(
        _app,
        max_bytes=150,
        max_anonymous_bytes=150,
        cookie_name="tb_session",
    )
    await wrapped({"type": "http", "headers": []}, _receive, _capture)
    assert [m["status"] for m in sent if m["type"] == "http.response.start"] == [200]


@pytest.mark.parametrize(
    "wrap",
    [
        SecurityHeadersMiddleware,
        lambda app: BodySizeLimitMiddleware(
            app,
            max_bytes=1,
            max_anonymous_bytes=1,
            cookie_name="tb_session",
        ),
    ],
)
@pytest.mark.asyncio
async def test_non_http_scopes_pass_straight_through(wrap: Callable[[ASGIApp], ASGIApp]) -> None:
    """Lifespan and websocket traffic has no headers to stamp and no body to count."""
    seen: list[str] = []

    async def _app(scope: Scope, _receive: Receive, _send: Send) -> None:
        seen.append(scope["type"])

    await wrap(_app)({"type": "lifespan"}, _idle_receive, _discard)
    assert seen == ["lifespan"]
