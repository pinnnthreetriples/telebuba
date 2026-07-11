"""Logs endpoint — thin cursor-paginated read over ``services.logs``."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi import status as http_status

from schemas.api import Page
from schemas.logs import LogEntry, LogFilter, LogPurgeResult, LogStatusFilter
from services import logs as logs_service

router = APIRouter(tags=["logs"])


@router.get("/logs", response_model=Page[LogEntry], operation_id="listLogs")
async def list_logs(
    status: LogStatusFilter = "all",
    account_id: str = "",
    event_prefix: str = "",
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> Page[LogEntry]:
    log_filter = LogFilter(
        status=status,
        account_id=account_id,
        event_prefix=event_prefix,
        limit=limit,
    )
    try:
        return await logs_service.list_logs_page(log_filter, cursor)
    except logs_service.InvalidCursorError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="invalid pagination cursor",
        ) from exc


@router.delete("/logs", response_model=LogPurgeResult, operation_id="clearLogs")
async def delete_logs(event_prefix: str = "") -> LogPurgeResult:
    """Clear log rows whose event starts with ``event_prefix`` (all rows when empty)."""
    return await logs_service.clear_logs(event_prefix)
