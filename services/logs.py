"""Business logic for the logs domain (read-only).

Pure async functions: accept a filter, hit ``core.db`` for the rows, compute
a small summary, return a Pydantic page state. The NiceGUI Logs page in
``features/logs.py`` calls this on every poll tick.

The underlying ``logs`` table is filled by ``core.logging.log_event`` — this
module never writes to it.
"""

from __future__ import annotations

from core.db import list_filtered_logs
from schemas.logs import LogEntry, LogFilter, LogsPageState, LogsSummary


async def load_logs_page(log_filter: LogFilter) -> LogsPageState:
    entries = await list_filtered_logs(log_filter)
    return LogsPageState(entries=entries, summary=_summarize(entries))


def _summarize(entries: list[LogEntry]) -> LogsSummary:
    return LogsSummary(
        total=len(entries),
        success=sum(entry.status == "success" for entry in entries),
        warning=sum(entry.status == "warning" for entry in entries),
        error=sum(entry.status == "error" for entry in entries),
    )
