"""Pydantic schemas for the three-tier logging system.

These types flow between ``core.logging`` and any future Logs page in
``features/``. The actual file sink (loguru) and Sentry init are encapsulated
in ``core/logging.py``; nothing outside that module imports loguru / sentry_sdk.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

LogLevel = Literal["INFO", "WARNING", "ERROR"]
LogStatus = Literal["success", "warning", "error"]


class LogEventInput(BaseModel):
    """Caller-supplied event payload."""

    model_config = ConfigDict(arbitrary_types_allowed=False, extra="forbid")

    level: LogLevel
    event: str = Field(min_length=1)
    account_id: str | None = None
    extra: dict[str, object] = Field(default_factory=dict)


class LogEntry(BaseModel):
    """One row of the ``logs`` SQLite table."""

    id: int
    created_at: str = Field(min_length=1)
    level: LogLevel
    status: LogStatus
    account_id: str | None
    event: str = Field(min_length=1)
    extra: dict[str, object] = Field(default_factory=dict)


LogStatusFilter = Literal["all", "success", "warning", "error"]


class LogFilter(BaseModel):
    """Caller-supplied filter for the Logs page."""

    model_config = ConfigDict(extra="forbid")

    status: LogStatusFilter = "all"
    account_id: str = ""
    limit: int = Field(default=200, ge=1, le=1000)
    # When True, drop ``success`` rows — i.e. only warnings + errors. Powers the
    # board's global "problems" feed (every account's failures in one place).
    problems_only: bool = False


class LogsSummary(BaseModel):
    """Counters shown above the Logs table — computed over the filtered window."""

    total: int = Field(ge=0)
    success: int = Field(ge=0)
    warning: int = Field(ge=0)
    error: int = Field(ge=0)


class LogsPageState(BaseModel):
    """Everything the Logs page renders in one go."""

    entries: list[LogEntry] = Field(default_factory=list)
    summary: LogsSummary
