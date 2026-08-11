"""ASGI wrappers installed ahead of routing, body parsing, and dependencies.

These are raw ASGI rather than route dependencies because a dependency resolves too
late. Starlette parses — and spools — the multipart body before
``Depends(get_current_user)`` runs, so an *unauthenticated* caller could make the
server buffer the whole configured maximum first. Measured against the unpatched
app: 3,145,867 body bytes consumed, then a 401.

``Content-Length`` is not the bound. That probe declared no length at all
(``Transfer-Encoding: chunked``), and the in-handler ``UploadFile.size`` guard only
ever sees the final measured part size — after the transfer. Counting the
``http.request`` messages as they arrive is the only limit chunked input cannot
walk past.

The large ceiling is granted only to an exact multipart upload route carrying a
valid, non-revoked session. A cookie name or a syntactically valid JWT is not
enough. The same early layer holds a bounded admission slot for the whole upload,
so concurrent multipart spooling and handler buffers are capped too.

Written against the bare ASGI signature: ``api/`` may import ``fastapi`` but not
``starlette`` (``tests/test_architecture.py::test_api_imports_only_allowlisted``),
and neither a byte counter nor a header stamp needs anything from either.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, MutableMapping, Sequence
from dataclasses import dataclass
from email.message import Message as EmailMessage
from typing import Any
from urllib.parse import urlsplit

from schemas.api import ErrorDetail, ErrorEnvelope

Message = MutableMapping[str, Any]
Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]
SessionValidator = Callable[[str], Awaitable[bool]]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BodyLimitPolicy:
    max_bytes: int
    max_anonymous_bytes: int
    cookie_name: str
    max_concurrent_uploads: int = 1
    large_upload_path_patterns: Sequence[str] = ()


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
_HTTP_FORBIDDEN = 403
_HTTP_TOO_MANY_REQUESTS = 429
# The exception's ``str``, never the wire (the 413 body above is that). Deliberately
# not named ``*_REASON``: it is not an operator-facing ``extra["reason"]`` code, and
# ``tests/test_logevent_i18n_parity`` rightly reads every such name as one.
_TOO_LARGE_MESSAGE = "request body exceeded the configured limit"
_UPLOAD_BUSY = (
    ErrorEnvelope(
        error=ErrorDetail(code="upload_capacity_exceeded", message="upload_capacity_exceeded"),
    )
    .model_dump_json(exclude_none=True)
    .encode()
)
_ORIGIN_FORBIDDEN = (
    ErrorEnvelope(error=ErrorDetail(code="forbidden", message="untrusted_origin"))
    .model_dump_json(exclude_none=True)
    .encode()
)
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
_MAX_MULTIPART_BOUNDARY_CHARS = 70
_VISIBLE_ASCII_MIN = 32
_ASCII_DELETE = 127

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
    """Refuse a request whose body exceeds its budget, counting as it arrives.

    The large budget requires an exact upload route and a session accepted by
    ``validate_session``. Everything else receives ``max_anonymous_bytes``.

    On overflow the caller gets a 413 and the wrapped app's ``receive()`` fails, so
    it unwinds instead of parsing bytes we already refused. Whatever it then tries
    to answer (FastAPI maps a body-read failure to 400) is dropped: the 413 is the
    real response.
    """

    def __init__(
        self,
        app: ASGIApp,
        policy: BodyLimitPolicy,
        validate_session: SessionValidator | None = None,
    ) -> None:
        self.app = app
        self.max_bytes = policy.max_bytes
        self.max_anonymous_bytes = policy.max_anonymous_bytes
        self._cookie_name = policy.cookie_name.encode()
        self._upload_patterns = tuple(
            re.compile(pattern) for pattern in policy.large_upload_path_patterns
        )
        self._validate_session = validate_session
        self._upload_gate = asyncio.Semaphore(policy.max_concurrent_uploads)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        authenticated_upload = await self._is_authenticated_upload(scope)
        budget = self.max_bytes if authenticated_upload else self.max_anonymous_bytes
        admitted = False
        if authenticated_upload:
            # No await between the state check and acquire: within one event loop
            # this is an atomic try-acquire, so excess uploads fail before body read.
            if self._upload_gate.locked():
                await _send_json(send, _HTTP_TOO_MANY_REQUESTS, _UPLOAD_BUSY, retry_after="1")
                return
            await self._upload_gate.acquire()
            admitted = True
        counted = _BodyCounter(receive, max_bytes=budget)
        try:
            await self._run_counted(scope, counted, send)
        finally:
            if admitted:
                self._upload_gate.release()

    async def _run_counted(self, scope: Scope, counted: _BodyCounter, send: Send) -> None:
        started = False

        async def guarded_send(message: Message) -> None:
            nonlocal started
            if counted.rejected:
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counted.receive, guarded_send)
        except Exception:
            # Ours, or something the app derived from ours (FastAPI catches a body
            # read failure and re-raises it as an HTTP error). Anything unrelated to
            # the refusal is a genuine app error and must keep propagating.
            if not counted.rejected:
                raise
        if counted.rejected and not started:
            await _send_json(send, _HTTP_REQUEST_TOO_LARGE, _TOO_LARGE)

    async def _is_authenticated_upload(self, scope: Scope) -> bool:
        if not self._is_large_upload_route(scope):
            return False
        token = _cookie_value(scope, self._cookie_name)
        if token is None or self._validate_session is None:
            return False
        try:
            return await self._validate_session(token.decode(errors="ignore"))
        except Exception as exc:  # noqa: BLE001 -- admission must fail closed
            # Authentication infrastructure is part of admission. A transient DB
            # failure must never promote the request to the 210 MB budget or leak
            # an unwrapped exception from this outer ASGI layer.
            logger.warning(
                "upload session validation failed (%s)",
                type(exc).__name__,
            )
            return False

    def _is_large_upload_route(self, scope: Scope) -> bool:
        if str(scope.get("method", "")).upper() != "POST":
            return False
        if not _has_valid_multipart_content_type(scope):
            return False
        path = str(scope.get("path", ""))
        return any(pattern.fullmatch(path) for pattern in self._upload_patterns)


def _cookie_value(scope: Scope, name: bytes) -> bytes | None:
    """Return one exact cookie value; ambiguity never counts as authentication."""
    values = _cookie_values(scope, name)
    if len(values) != 1:
        return None
    return values[0] or None


def _cookie_values(scope: Scope, name: bytes) -> list[bytes]:
    """Collect exact matches across every Cookie header and cookie-pair."""
    values: list[bytes] = []
    for header, value in scope.get("headers", ()):
        if header.lower() != b"cookie":
            continue
        for part in value.split(b";"):
            key, separator, cookie_value = part.partition(b"=")
            if separator and key.strip() == name:
                values.append(cookie_value.strip())
    return values


def _header_values(scope: Scope, name: bytes) -> list[bytes]:
    return [value for header, value in scope.get("headers", ()) if header.lower() == name]


def _has_valid_multipart_content_type(scope: Scope) -> bool:
    """Require one multipart Content-Type with a syntactically usable boundary."""
    values = _header_values(scope, b"content-type")
    if len(values) != 1:
        return False
    try:
        parsed = EmailMessage()
        parsed["content-type"] = values[0].decode("latin-1")
        parameters = parsed.get_params(header="content-type", unquote=True) or []
        boundaries = [
            value
            for key, value in parameters[1:]
            if key.lower() == "boundary" and isinstance(value, str)
        ]
        boundary = parsed.get_boundary()
    except (UnicodeError, ValueError):
        return False
    return bool(
        parsed.get_content_type().lower() == "multipart/form-data"
        and len(boundaries) == 1
        and boundary
        and boundary == boundaries[0]
        and len(boundary) <= _MAX_MULTIPART_BOUNDARY_CHARS
        and not boundary.endswith(" ")
        and all(_VISIBLE_ASCII_MIN < ord(character) < _ASCII_DELETE for character in boundary)
    )


class _BodyCounter:
    """Tallies ``http.request`` body bytes and cuts the stream off past the cap.

    Rejection lands on the message that crosses the cap, so the overshoot is one
    ASGI message. How big that is belongs to the SERVER, not to this class: uvicorn
    pauses reading once ``flow_control.HIGH_WATER_LIMIT`` (65,536) is buffered, so
    in production the overshoot is ~64 KiB. An in-process transport that hands over
    one enormous message will see one enormous overshoot, which is a property of
    that transport rather than a hole here.
    """

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


async def _send_json(
    send: Send,
    status: int,
    body: bytes,
    *,
    retry_after: str | None = None,
) -> None:
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
    ]
    if retry_after is not None:
        headers.append((b"retry-after", retry_after.encode()))
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": headers,
        },
    )
    await send({"type": "http.response.body", "body": body})


class OriginProtectionMiddleware:
    """Reject cross-origin unsafe requests that carry the session cookie.

    Exact comparison against the request origin and configured SPA origins
    prevents a same-site sibling subdomain from using the HttpOnly cookie as
    ambient auth. Cookie-authenticated unsafe requests without exactly one Origin
    are refused: this API has no bearer-authenticated non-browser write path.
    """

    def __init__(self, app: ASGIApp, *, cookie_name: str, allowed_origins: Sequence[str]) -> None:
        self.app = app
        self._cookie_name = cookie_name.encode()
        self._allowed_origins = frozenset(
            normalised for origin in allowed_origins if (normalised := _normalise_origin(origin))
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or str(scope.get("method", "")).upper() in _SAFE_METHODS:
            await self.app(scope, receive, send)
            return
        cookie_values = _cookie_values(scope, self._cookie_name)
        if not cookie_values:
            await self.app(scope, receive, send)
            return
        # Multiple values can be interpreted differently by ASGI consumers. Do
        # not let one layer validate a different session from the route layer.
        if len(cookie_values) != 1 or not cookie_values[0]:
            await _send_json(send, _HTTP_FORBIDDEN, _ORIGIN_FORBIDDEN)
            return

        host_values = _header_values(scope, b"host")
        request_origin = _request_origin(scope, host_values[0] if len(host_values) == 1 else None)
        allowed = self._allowed_origins | ({request_origin} if request_origin else set())
        origins = _header_values(scope, b"origin")
        candidate = (
            _normalise_origin(origins[0].decode(errors="ignore")) if len(origins) == 1 else ""
        )
        if not candidate or candidate not in allowed:
            await _send_json(send, _HTTP_FORBIDDEN, _ORIGIN_FORBIDDEN)
            return
        await self.app(scope, receive, send)


def _request_origin(scope: Scope, host: bytes | None) -> str:
    if host is None:
        return ""
    return _normalise_origin(f"{scope.get('scheme', 'http')}://{host.decode(errors='ignore')}")


def _normalise_origin(value: str, *, allow_path: bool = False) -> str:
    """Canonical scheme+authority, or an invalid sentinel that never matches."""
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or (
                not allow_path and (parsed.path not in {"", "/"} or parsed.query or parsed.fragment)
            )
        ):
            return ""
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
    except ValueError:
        return ""


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


__all__ = [
    "BodyLimitPolicy",
    "BodySizeLimitMiddleware",
    "OriginProtectionMiddleware",
    "SecurityHeadersMiddleware",
]
