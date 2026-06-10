from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SessionCheckStatus = Literal[
    "alive",
    "unauthorized",
    "session_error",
    "account_error",
    "flood_wait",
    "network_error",
    "proxy_error",
    "unknown_error",
]


class TelegramSessionCheckRequest(BaseModel):
    account_id: str = Field(min_length=1)
    session_name: str | None = Field(default=None, min_length=1)


class TelegramSessionCheckResult(BaseModel):
    account_id: str = Field(min_length=1)
    session_path: str = Field(min_length=1)
    status: SessionCheckStatus
    is_temporary: bool
    user_id: int | None = None
    phone: str | None = None
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    flood_wait_seconds: int | None = Field(default=None, ge=0)
