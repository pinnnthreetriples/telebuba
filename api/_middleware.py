"""ASGI wrappers the app installs ahead of routing, so nothing can slip past them.

Both are raw ASGI rather than route dependencies because a dependency resolves too
late. Starlette parses — and spools — the multipart body before
``Depends(get_current_user)`` runs, so an *unauthenticated* caller could make the
server buffer the whole configured maximum first. Measured against the unpatched
app: 3,145,867 body bytes consumed, then a 401.

``Content-Length`` is not the bound. That probe declared no length at all
(``Transfer-Encoding: chunked``), and the in-handler ``UploadFile.size`` guard only
ever sees the final measured part size — after the transfer. Counting the
``http.request`` messages as they arrive is the only limit chunked input cannot
walk past.

Written against the bare ASGI signature: ``api/`` may import ``fastapi`` but not
``starlette`` (``tests/test_architecture.py::test_api_imports_only_allowlisted``),
and neither a byte counter nor a header stamp needs anything from either.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from schemas.api import ErrorDetail, ErrorEnvelope

Message = MutableMapping[str, Any]
Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

# Locale-neutral refusal, same envelope every other error uses. ``message`` is the
# code, not prose (the SPA owns the wording) — mirrors the ``validation_error``
# precedent in ``api.errors``.
_TOO_LARGE = (
    ErrorEnvelope(
        error=ErrorDetail(code="payload_too_large", message="payload_too_large"),
    )
    .model_dump_json(exclude_none=True)
    .encode()
)
_HTTP_REQUEST_TOO_LARGE = 413
# The exception's ``str``, never the wire (the 413 body above is that). Deliberately
# not named ``*_REASON``: it is not an operator-facing ``extra["reason"]`` code, and
# ``tests/test_logevent_i18n_parity`` rightly reads every such name as one.
_TOO_LARGE_MESSAGE = "request body exceeded the configured limit"

# Hardening headers. Deliberately NOT a full CSP: ``frame-ancestors`` governs who
# may embed us and cannot break the SPA's own script/style loading, whereas a
# default-src policy would need the built bundle's real hashes to stay correct.
# ``X-Frame-Options`` rides along for pre-CSP2 clients.
_SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"content-security-policy", b"frame-ancestors 'none'"),
    (b"referrer-policy", b"no-referrer"),
    (b"cross-origin-opener-policy", b"same-origin"),
)


class _BodyLimitExceededError(OSError):
    """Raised out of the wrapped ``receive()`` once the body passes the cap.

    An ``OSError`` subclass deliberately. Starlette's multipart parser closes the
    ``SpooledTemporaryFile`` it has already opened for exactly two exception types,
    ``MultiPartException`` and ``OSError``; anything else — including the
    ``ClientDisconnect`` a synthetic ``http.disconnect`` produces — leaves that temp
    file for the garbage collector. On a path a caller can trigger at will by
    oversizing the request, "closed now" is the only acceptable answer, and ``api/``
    may not import Starlette's own type. "The body stream failed" is a fair reading
    of ``OSError`` in any case.
    """


class BodySizeLimitMiddleware:
    """Refuse a request whose body exceeds ``max_bytes``, counting as it arrives.

    On overflow the caller gets a 413 and the wrapped app's ``receive()`` fails, so
    it unwinds instead of parsing bytes we already refused. Whatever it then tries
    to answer (FastAPI maps a body-read failure to 400) is dropped: the 413 is the
    real response.
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        counted = _BodyCounter(receive, max_bytes=self.max_bytes)

        async def guarded_send(message: Message) -> None:
            if not counted.rejected:
                await send(message)

        try:
            await self.app(scope, counted.receive, guarded_send)
        except Exception:
            # Ours, or something the app derived from ours (FastAPI catches a body
            # read failure and re-raises it as an HTTP error). Anything unrelated to
            # the refusal is a genuine app error and must keep propagating to
            # Starlette's ServerErrorMiddleware.
            if not counted.rejected:
                raise
        if counted.rejected:
            await _send_too_large(send)


class _BodyCounter:
    """Tallies ``http.request`` body bytes and cuts the stream off past the cap."""

    def __init__(self, receive: Receive, *, max_bytes: int) -> None:
        self._receive = receive
        self._max_bytes = max_bytes
        self._received = 0
        self.rejected = False

    async def receive(self) -> Message:
        if self.rejected:
            raise _BodyLimitExceededError(_TOO_LARGE_MESSAGE)
        message = await self._receive()
        if message["type"] != "http.request":
            return message
        self._received += len(message.get("body", b""))
        if self._received > self._max_bytes:
            self.rejected = True
            raise _BodyLimitExceededError(_TOO_LARGE_MESSAGE)
        return message


async def _send_too_large(send: Send) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": _HTTP_REQUEST_TOO_LARGE,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(_TOO_LARGE)).encode()),
            ],
        },
    )
    await send({"type": "http.response.body", "body": _TOO_LARGE})


class SecurityHeadersMiddleware:
    """Stamp the hardening headers onto every response.

    Outermost of our two, so it also covers the responses no router produces: the
    413 above, 404s, and the static SPA files the composition root serves.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _value in headers}
                # Never overwrite: a response that set its own policy meant it.
                headers.extend((n, v) for n, v in _SECURITY_HEADERS if n not in present)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


__all__ = ["BodySizeLimitMiddleware", "SecurityHeadersMiddleware"]
