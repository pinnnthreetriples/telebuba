"""Origin checks for cookie-authenticated unsafe requests."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from api._middleware import (
    _cookie_values,
    _header_values,
    _send_json,
)
from schemas.api import ErrorDetail, ErrorEnvelope

if TYPE_CHECKING:
    from collections.abc import Sequence

    from api._middleware import ASGIApp, Receive, Scope, Send

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
_HTTP_FORBIDDEN = 403
_ORIGIN_FORBIDDEN = (
    ErrorEnvelope(error=ErrorDetail(code="forbidden", message="untrusted_origin"))
    .model_dump_json(exclude_none=True)
    .encode()
)


class OriginProtectionMiddleware:
    """Reject cross-origin unsafe requests that carry the session cookie.

    Exact comparison against the request origin and configured SPA origins
    prevents a same-site sibling subdomain from using the HttpOnly cookie as
    ambient auth. Cookie-authenticated unsafe requests without exactly one Origin
    are refused: this API has no bearer-authenticated non-browser write path.

    BREAKING for scripted clients. The session cookie is the only credential this
    API has, so a script that writes with it — ``curl -b "tb_session=..." -X POST``
    — now gets a bare 403 (``untrusted_origin``) unless it also sends an ``Origin``
    header the server trusts: ``-H "Origin: <the origin the request is addressed
    to>"``. Fail-closed is deliberate; a missing Origin is exactly what a CSRF
    request from an old browser looks like, and there is no second credential that
    could tell the two apart. Reads (``GET``/``HEAD``/``OPTIONS``) are untouched.
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
