"""Business logic for the logs domain.

Pure async functions: accept a filter, hit ``core.db`` for the rows, compute
a small summary, return a Pydantic page state. The NiceGUI Logs page in
``features/logs.py`` calls this on every poll tick.

Reads only, except ``clear_logs`` (the operator "clear logs" action); rows are
otherwise written solely by ``core.logging.log_event``.
"""

from __future__ import annotations

from core.db import list_filtered_logs, purge_logs
from schemas.api import Page
from schemas.logs import LogEntry, LogFilter, LogPurgeResult, LogsPageState, LogsSummary


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


async def clear_logs(event_prefix: str = "") -> LogPurgeResult:
    """Delete the log rows matching ``event_prefix`` (all rows when empty); return the count."""
    deleted = await purge_logs(event_prefix)
    return LogPurgeResult(deleted=deleted)


def _summarize(entries: list[LogEntry]) -> LogsSummary:
    return LogsSummary(
        total=len(entries),
        success=sum(entry.status == "success" for entry in entries),
        warning=sum(entry.status == "warning" for entry in entries),
        error=sum(entry.status == "error" for entry in entries),
    )
