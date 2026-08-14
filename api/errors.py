"""Error-envelope mapping for the API.

Every error response is the cross-cutting envelope ``{error:{code,message,fields?}}``
(:class:`schemas.api.ErrorEnvelope`). FastAPI's default 422 validation error is
remapped into the same shape; raised ``HTTPException``s and any unexpected
exception are mapped too, so the wire contract has exactly one error shape.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from core.logging import log_event
from schemas.api import ErrorDetail, ErrorEnvelope
from services.accounts import AccountActionError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fastapi import FastAPI, Request

# Stdlib sink for full third-party text — see ``core.proxy_check._failed_result``.
logger = logging.getLogger(__name__)

# Stable error codes per HTTP status (the locale-neutral contract: the SPA maps
# codes to text). Anything unmapped falls back to a generic "http_error".
_HTTP_ERROR_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    429: "rate_limited",
    503: "unavailable",
}

# One description per status, so a route declares the statuses it can answer and
# nothing else. Declaring a status at all is what makes the generated TypeScript
# client TYPE the error body instead of the SPA hand-casting it; declaring 422 in
# particular replaces FastAPI's auto ``HTTPValidationError`` — whose ``detail`` key
# ``_handle_validation_error`` below overwrites, so it never reaches the wire.
_ERROR_DESCRIPTIONS: dict[int, str] = {
    400: "Bad request, or Telegram refused the action",
    401: "Not authenticated",
    404: "Not found",
    409: "Conflict with the current state",
    422: "Request validation failed",
    429: "Too many requests",
    500: "Internal server error",
    503: "Upstream gateway unavailable",
}


def error_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI ``responses`` fragment declaring ``ErrorEnvelope`` for each status.

    Routes compose these fragments so the schema lists exactly the statuses their
    code can answer. ``tests/test_api_error_contract.py`` recomputes that reachable
    set from the routes, dependencies and registered handlers and fails on any
    difference, so an under- or over-declared operation cannot ship.
    """
    return {
        status: {"model": ErrorEnvelope, "description": _ERROR_DESCRIPTIONS[status]}
        for status in statuses
    }


# Every operation behind the session gate: 401 (``api.deps.get_current_user``),
# 422 (request validation) and 500 (``_handle_unexpected``). Attached once, where
# the guarded routers are mounted (``api.v1.__init__``).
PROTECTED_ERRORS = error_responses(401, 422, 500)
# Every route wrapped in ``api.v1._errors.service_errors_to_http``: 404
# (``AccountNotFoundError``), 400 (a service ``ValueError``) and 400/503 from
# ``AccountActionError`` via ``_handle_account_action_error`` below.
SERVICE_ERRORS = error_responses(400, 404, 503)


def _envelope(
    *,
    code: str,
    message: str,
    status_code: int,
    fields: dict[str, str] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = ErrorEnvelope(error=ErrorDetail(code=code, message=message, fields=fields))
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(exclude_none=True),
        headers=headers,
    )


async def _handle_http_exception(_request: Request, exc: HTTPException) -> JSONResponse:
    code = _HTTP_ERROR_CODES.get(exc.status_code, "http_error")
    return _envelope(
        code=code,
        message=str(exc.detail),
        status_code=exc.status_code,
        headers=exc.headers,
    )


async def _handle_account_action_error(
    _request: Request,
    exc: AccountActionError,
) -> JSONResponse:
    # Telegram refused the action: ``message`` is the stable code (the SPA
    # translates it); a flood-family retry duration travels in ``fields``
    # instead of being dropped with the str() collapse. A partially-completed
    # channel_create rides its already-created channel id along the same way,
    # so the UI can adopt the private channel instead of re-creating it.
    extra: dict[str, str] = {}
    if exc.retry_after_seconds is not None:
        extra["retry_after_seconds"] = str(exc.retry_after_seconds)
    if exc.channel_id is not None:
        extra["channel_id"] = exc.channel_id
    fields = extra or None
    if exc.code == "unavailable":
        # Gateway infrastructure failure (pool/socket/timeout): a server-side
        # outage, not a client fault — 503 so the SPA offers retry instead of
        # blaming the input.
        return _envelope(code="unavailable", message=exc.code, status_code=503, fields=fields)
    return _envelope(code="bad_request", message=exc.code, status_code=400, fields=fields)


async def _handle_validation_error(
    _request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    fields = {".".join(str(part) for part in err["loc"]): err["msg"] for err in exc.errors()}
    # ``message`` is a locale-neutral CODE like every other envelope message
    # (non-negotiable #12) — the SPA renders it verbatim as the toast fallback, so
    # English prose here reached the operator's UI untranslated. The per-field
    # reasons stay in ``fields`` keyed ``body.<name>`` / ``query.<name>``.
    return _envelope(
        code="validation_error",
        message="validation_error",
        status_code=422,
        fields=fields,
    )


async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    # Last line of defense: never leak a stack trace to the client. Log it
    # (best-effort) and return the generic envelope.
    #
    # The broadest ``extra`` in the project: this fires on ANY unexpected exception,
    # so ``repr(exc)`` could be a proxy URL with credentials or a ``.session`` path —
    # and ``extra`` is served back by ``GET /logs`` and streamed by ``GET /events``,
    # so the "never leak to the client" rule above was defeated by a second route.
    # Class name in ``extra``, whole traceback to stderr.
    logger.error("unhandled exception on %s", request.url.path, exc_info=exc)
    await log_event(
        "ERROR",
        "api_unhandled_exception",
        extra={"path": request.url.path, "error_type": type(exc).__name__},
    )
    return _envelope(code="internal_error", message="Internal server error", status_code=500)


def register_error_handlers(app: FastAPI) -> None:
    # Starlette types handlers as ``(Request, Exception)``; our handlers narrow the
    # second arg to the exact class they're registered for (correct at runtime).
    # api/ may not import starlette's ``ExceptionHandler`` type (allowlist), so the
    # contravariance is documented here rather than satisfied via a cast.
    app.add_exception_handler(HTTPException, _handle_http_exception)  # ty: ignore[invalid-argument-type]
    app.add_exception_handler(AccountActionError, _handle_account_action_error)  # ty: ignore[invalid-argument-type]
    app.add_exception_handler(RequestValidationError, _handle_validation_error)  # ty: ignore[invalid-argument-type]
    app.add_exception_handler(Exception, _handle_unexpected)
