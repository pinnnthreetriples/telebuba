"""Error-envelope mapping for the API.

Every error response is the cross-cutting envelope ``{error:{code,message,fields?}}``
(:class:`schemas.api.ErrorEnvelope`). FastAPI's default 422 validation error is
remapped into the same shape; raised ``HTTPException``s and any unexpected
exception are mapped too, so the wire contract has exactly one error shape.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from core.logging import log_event
from schemas.api import ErrorDetail, ErrorEnvelope
from services.accounts import AccountActionError

if TYPE_CHECKING:
    from fastapi import FastAPI, Request

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


def _envelope(
    *,
    code: str,
    message: str,
    status_code: int,
    fields: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorEnvelope(error=ErrorDetail(code=code, message=message, fields=fields))
    return JSONResponse(status_code=status_code, content=body.model_dump(exclude_none=True))


async def _handle_http_exception(_request: Request, exc: HTTPException) -> JSONResponse:
    code = _HTTP_ERROR_CODES.get(exc.status_code, "http_error")
    return _envelope(code=code, message=str(exc.detail), status_code=exc.status_code)


async def _handle_account_action_error(
    _request: Request,
    exc: AccountActionError,
) -> JSONResponse:
    # Telegram refused the action: ``message`` is the stable code (the SPA
    # translates it); a flood-family retry duration travels in ``fields``
    # instead of being dropped with the str() collapse.
    fields = (
        {"retry_after_seconds": str(exc.retry_after_seconds)}
        if exc.retry_after_seconds is not None
        else None
    )
    return _envelope(code="bad_request", message=exc.code, status_code=400, fields=fields)


async def _handle_validation_error(
    _request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    fields = {".".join(str(part) for part in err["loc"]): err["msg"] for err in exc.errors()}
    return _envelope(
        code="validation_error",
        message="Request validation failed",
        status_code=422,
        fields=fields,
    )


async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    # Last line of defense: never leak a stack trace to the client. Log it
    # (best-effort) and return the generic envelope.
    await log_event(
        "ERROR",
        "api_unhandled_exception",
        extra={"path": request.url.path, "error": repr(exc)},
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
