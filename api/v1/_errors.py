"""Route-level error mapping shared by the accounts routers."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from fastapi import HTTPException
from fastapi import status as http_status
from fastapi.exceptions import RequestValidationError

from services import accounts

if TYPE_CHECKING:
    from collections.abc import Iterator


def _as_request_validation_error(exc: ValueError) -> RequestValidationError | None:
    """A Pydantic ``ValidationError`` re-shaped as FastAPI's own request error.

    Request models assembled inside a route — the multipart uploads, whose fields
    arrive as separate ``Form``/``File`` params rather than one body model — are
    validated by us, not by FastAPI's binding step, so their refusal reaches this
    mapper as a plain ``ValueError``. Left to the generic collapse below it would
    answer 400 with Pydantic's multi-line English prose naming the model: an
    unbounded third-party message on the wire (non-negotiable #12) for what is
    simply a malformed request. Re-raised as ``RequestValidationError`` it gets the
    same 422 ``validation_error`` envelope every other malformed request gets.

    Recognised by its ``errors()`` accessor rather than by importing Pydantic:
    ``tests/test_architecture.py`` allows api/ to import only services, schemas,
    fastapi, the stdlib and ``core.config``/``core.logging``. ``loc`` is prefixed
    with ``body`` so the field keys match FastAPI's native ones (``body.caption``).
    """
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return None
    return RequestValidationError(
        [{**err, "loc": ("body", *err.get("loc", ()))} for err in errors()],
    )


@contextmanager
def service_errors_to_http() -> Iterator[None]:
    """Map service ``ValueError``s to 400, passing ``AccountActionError`` through.

    ``AccountActionError`` subclasses ``ValueError`` but must reach its dedicated
    handler in :mod:`api.errors` (stable code + retry seconds in the envelope), so
    it is re-raised untouched before the generic ``str(exc)`` collapse.
    ``AccountNotFoundError`` is a missing row, not a bad request, so it maps to
    404 (it is a ``LookupError``, deliberately outside the ``ValueError`` family).
    A Pydantic ``ValidationError`` is a request-shape error, not a service refusal,
    so it becomes the 422 envelope (see :func:`_as_request_validation_error`).
    """
    try:
        yield
    except accounts.AccountNotFoundError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"account not found: {exc}",
        ) from exc
    except accounts.AccountActionError:
        raise
    except ValueError as exc:
        validation = _as_request_validation_error(exc)
        if validation is not None:
            raise validation from exc
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
