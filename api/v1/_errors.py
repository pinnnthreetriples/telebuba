"""Route-level error mapping shared by the accounts routers."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from fastapi import HTTPException
from fastapi import status as http_status

from services import accounts

if TYPE_CHECKING:
    from collections.abc import Iterator


@contextmanager
def service_errors_to_http() -> Iterator[None]:
    """Map service ``ValueError``s to 400, passing ``AccountActionError`` through.

    ``AccountActionError`` subclasses ``ValueError`` but must reach its dedicated
    handler in :mod:`api.errors` (stable code + retry seconds in the envelope), so
    it is re-raised untouched before the generic ``str(exc)`` collapse.
    """
    try:
        yield
    except accounts.AccountActionError:
        raise
    except ValueError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
