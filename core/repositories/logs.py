"""Logs repository (split out of core.db for #38).

Owns reads/writes of the ``logs`` table. Shared plumbing (engine, table object,
generic row helpers) is imported from ``core.db``; the public async functions are
re-exported by ``core.db`` so existing call sites are unaffected.
"""

from __future__ import annotations

import asyncio
import json
from typing import cast

from sqlalchemy import delete, insert, select

from core.db import _get_engine, _logs, _now_iso, _optional_str
from schemas.logs import LogEntry, LogEventInput, LogFilter, LogLevel, LogStatus

_STATUS_BY_LEVEL: dict[LogLevel, LogStatus] = {
    "INFO": "success",
    "WARNING": "warning",
    "ERROR": "error",
}


def _insert_log_row(event: LogEventInput) -> LogEntry:
    values = {
        "created_at": _now_iso(),
        "level": event.level,
        "status": _STATUS_BY_LEVEL[event.level],
        "account_id": event.account_id,
        "event": event.event,
        "extra": json.dumps(event.extra, default=str, sort_keys=True),
    }
    with _get_engine().begin() as connection:
        result = connection.execute(insert(_logs).values(**values))
    primary_key = result.inserted_primary_key
    if primary_key is None:
        msg = "Insert into logs returned no primary key"
        raise RuntimeError(msg)
    inserted_id = int(primary_key[0])
    return LogEntry(
        id=inserted_id,
        created_at=str(values["created_at"]),
        level=event.level,
        status=_STATUS_BY_LEVEL[event.level],
        account_id=event.account_id,
        event=event.event,
        extra=event.extra,
    )


async def insert_log_row(event: LogEventInput) -> LogEntry:
    """Persist one log event into the SQLite ``logs`` table and return the row."""
    return await asyncio.to_thread(_insert_log_row, event)


def _list_recent_logs(limit: int) -> list[LogEntry]:
    statement = select(_logs).order_by(_logs.c.id.desc()).limit(limit)
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    entries: list[LogEntry] = []
    for row in rows:
        raw_extra = row["extra"]
        extra: dict[str, object] = json.loads(raw_extra) if raw_extra else {}
        entries.append(
            LogEntry(
                id=int(cast("int | str", row["id"])),
                created_at=str(row["created_at"]),
                level=cast("LogLevel", row["level"]),
                status=cast("LogStatus", row["status"]),
                account_id=_optional_str(row["account_id"]),
                event=str(row["event"]),
                extra=extra,
            ),
        )
    return entries


async def list_recent_logs(limit: int = 100) -> list[LogEntry]:
    """Return the latest log entries (newest first). Used by the future Logs page."""
    return await asyncio.to_thread(_list_recent_logs, limit)


def _list_filtered_logs(log_filter: LogFilter) -> list[LogEntry]:
    statement = select(_logs).order_by(_logs.c.id.desc()).limit(log_filter.limit)
    if log_filter.status != "all":
        statement = statement.where(_logs.c.status == log_filter.status)
    if log_filter.problems_only:
        statement = statement.where(_logs.c.status != "success")
    if log_filter.account_id:
        statement = statement.where(_logs.c.account_id == log_filter.account_id)
    if log_filter.event_prefix:
        statement = statement.where(_logs.c.event.like(f"{log_filter.event_prefix}%"))
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    entries: list[LogEntry] = []
    for row in rows:
        raw_extra = row["extra"]
        extra: dict[str, object] = json.loads(raw_extra) if raw_extra else {}
        entries.append(
            LogEntry(
                id=int(cast("int | str", row["id"])),
                created_at=str(row["created_at"]),
                level=cast("LogLevel", row["level"]),
                status=cast("LogStatus", row["status"]),
                account_id=_optional_str(row["account_id"]),
                event=str(row["event"]),
                extra=extra,
            ),
        )
    return entries


async def list_filtered_logs(log_filter: LogFilter) -> list[LogEntry]:
    """Return the latest log entries that match the filter (newest first)."""
    return await asyncio.to_thread(_list_filtered_logs, log_filter)


def _purge_logs_older_than(cutoff_iso: str) -> int:
    statement = delete(_logs).where(_logs.c.created_at < cutoff_iso)
    with _get_engine().begin() as connection:
        return connection.execute(statement).rowcount


async def purge_logs_older_than(cutoff_iso: str) -> int:
    """Delete log rows with ``created_at`` older than the given ISO timestamp.

    Returns the number of rows removed so callers can log / surface the cleanup
    in a single line. ISO comparison is lexicographic; same shape as everywhere
    else in the codebase.
    """
    return await asyncio.to_thread(_purge_logs_older_than, cutoff_iso)
