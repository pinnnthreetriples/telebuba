"""Business logic for the logs domain.

Pure async functions: accept a filter, hit ``core.db`` for the rows, compute
a small summary, return a Pydantic page state. The NiceGUI Logs page in
``features/logs.py`` calls this on every poll tick.

Reads only, except ``clear_logs`` (the operator "clear logs" action); rows are
otherwise written solely by ``core.logging.log_event``.
"""

from __future__ import annotations

from core.db import count_logs, list_filtered_logs, purge_logs
from core.logging import log_event
from schemas.api import Page
from schemas.logs import (
    LogCountResult,
    LogEntry,
    LogFilter,
    LogPurgeResult,
    LogsPageState,
    LogsSummary,
)


class InvalidCursorError(ValueError):
    """A pagination cursor that cannot be decoded into an offset."""


def _decode_cursor(cursor: str | None) -> int:
    # Opaque offset token (same shape as services.accounts); the client never parses it.
    if cursor is None:
        return 0
    try:
        offset = int(cursor)
    except ValueError as exc:
        raise InvalidCursorError(cursor) from exc
    if offset < 0:
        raise InvalidCursorError(cursor)
    return offset


async def load_logs_page(log_filter: LogFilter) -> LogsPageState:
    entries = await list_filtered_logs(log_filter)
    return LogsPageState(entries=entries, summary=_summarize(entries))


async def list_logs_page(log_filter: LogFilter, cursor: str | None = None) -> Page[LogEntry]:
    """One cursor-paginated page of log rows (newest first) for the API."""
    offset = _decode_cursor(cursor)
    probe = log_filter.model_copy(update={"limit": log_filter.limit + 1})
    rows = await list_filtered_logs(probe, offset=offset)
    has_more = len(rows) > log_filter.limit
    items = rows[: log_filter.limit]
    next_cursor = str(offset + log_filter.limit) if has_more else None
    return Page(items=items, next_cursor=next_cursor)


async def count_matching_logs(event_prefix: str = "") -> LogCountResult:
    """How many rows :func:`clear_logs` would delete for the same ``event_prefix``."""
    return LogCountResult(matching=await count_logs(event_prefix))


async def clear_logs(event_prefix: str = "") -> LogPurgeResult:
    """Delete the rows matching ``event_prefix``; return how many went.

    The purge leaves an audit row behind. One press of "clear logs" once erased a
    month of neurocomment history with nothing recording that it had happened, and
    the silence read as a broken system for days. Two properties make the row
    survive: it is written AFTER the delete, and its code carries no domain prefix
    any feed purges — a ``neurocomment_*`` code would be wiped by the next press of
    the very button it documents.
    """
    deleted = await purge_logs(event_prefix)
    if deleted:
        # No row for a no-op press, as the retention sweeps do (``services.warming
        # ._purge``, ``services.neurocomment._sweep``): an operator clicking an
        # already-empty feed would otherwise fill it with its own clear events.
        await log_event(
            "INFO",
            "logs_cleared",
            # ``*`` rather than "" so the whole-table case is legible in the row the
            # operator reads, instead of an absent value they have to interpret.
            extra={"deleted": deleted, "event_prefix": event_prefix or "*"},
        )
    return LogPurgeResult(deleted=deleted)


def _summarize(entries: list[LogEntry]) -> LogsSummary:
    return LogsSummary(
        total=len(entries),
        success=sum(entry.status == "success" for entry in entries),
        warning=sum(entry.status == "warning" for entry in entries),
        error=sum(entry.status == "error" for entry in entries),
    )
