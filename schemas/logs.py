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
