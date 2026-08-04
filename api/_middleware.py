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

One ceiling is not enough either, and this is what the first attempt got wrong.
The upload routes genuinely need ~200 MB for a ``tdata.zip``, so a single limit is
necessarily the largest budget any route needs — and 3.1 MB is 1.5% of it, so that
very probe still drained in full at the shipped default. The budget is therefore
split on whether the request carries a session cookie at all: no cookie, no
upload budget. Header inspection only — no DB, no signature check — because a
forged cookie merely buys back the 210 MB an attacker already has today, while a
caller who sends none is held to ``max_anonymous_request_bytes``. Route-based
scoping cannot help here: the defective route IS the upload route, and FastAPI
reads the body before ``solve_dependencies`` runs.

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
    """Refuse a request whose body exceeds its budget, counting as it arrives.

    The budget is ``max_bytes`` for a request carrying ``cookie_name``, and
    ``max_anonymous_bytes`` for one that does not — see the module docstring for
    why one number cannot do the job.

    On overflow the caller gets a 413 and the wrapped app's ``receive()`` fails, so
    it unwinds instead of parsing bytes we already refused. Whatever it then tries
    to answer (FastAPI maps a body-read failure to 400) is dropped: the 413 is the
    real response.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_bytes: int,
        max_anonymous_bytes: int,
        cookie_name: str,
    ) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.max_anonymous_bytes = max_anonymous_bytes
        self._cookie_name = cookie_name.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        budget = (
            self.max_bytes if _has_cookie(scope, self._cookie_name) else self.max_anonymous_bytes
        )
        counted = _BodyCounter(receive, max_bytes=budget)
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
            # the refusal is a genuine app error and must keep propagating to
            # Starlette's ServerErrorMiddleware.
            if not counted.rejected:
                raise
        # ``started`` guards a second ``http.response.start``, which is a protocol
        # error uvicorn rejects. Latent today — it needs a route that streams a
        # response and only then reads past the cap, and none does — but the cost of
        # being wrong about that is a 500 on a path meant to answer 413.
        if counted.rejected and not started:
            await _send_too_large(send)


def _has_cookie(scope: Scope, name: bytes) -> bool:
    """Whether the request carries a cookie called ``name``.

    Presence only. Nothing here validates or decodes the session — that is
    ``api.deps.get_current_user``'s job and it needs the body already parsed, which
    is the whole reason this runs first.
    """
    for header, value in scope.get("headers", ()):
        if header.lower() != b"cookie":
            continue
        # Compare the key, not a substring: ``x{name}=`` must not pass as ``{name}``.
        if any(part.partition(b"=")[0].strip() == name for part in value.split(b";")):
            return True
    return False


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
