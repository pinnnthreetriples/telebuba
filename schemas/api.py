"""Cross-cutting API wire contracts — error envelope + generic cursor pagination.

Per the split-stack ADR (2026-06-28): every error response is an ``ErrorEnvelope``
and every paginated list is a ``Page[T] = {items, next_cursor}``. These are pure
data types (no behaviour, no I/O) shared by ``api/`` routes and consumed by the
generated TypeScript client.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class HealthStatus(BaseModel):
    status: Literal["ok"] = "ok"


class ErrorDetail(BaseModel):
    code: str
    message: str
    # Field-level messages for a 422 (field path -> reason). Omitted otherwise.
    fields: dict[str, str] | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorDetail


class Page[T](BaseModel):
    """One page of a cursor-paginated list.

    ``next_cursor`` is an opaque token the client passes back to fetch the next
    page, or ``None`` when the current page is the last one.
    """

    items: list[T]
    next_cursor: str | None = None
