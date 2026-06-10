from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AccountStatus = Literal[
    "new",
    "alive",
    "unauthorized",
    "session_error",
    "account_error",
    "flood_wait",
    "network_error",
    "proxy_error",
    "unknown_error",
]


class AccountCreate(BaseModel):
    account_id: str = Field(min_length=1)
    label: str | None = Field(default=None, min_length=1)
    session_name: str | None = Field(default=None, min_length=1)


class AccountSessionFileImport(BaseModel):
    filename: str = Field(min_length=1)
    content: bytes = Field(min_length=1)
    label: str | None = Field(default=None, min_length=1)


class AccountRead(BaseModel):
    account_id: str = Field(min_length=1)
    label: str | None = None
    session_name: str | None = None
    status: AccountStatus
    user_id: int | None = None
    phone: str | None = None
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    last_checked_at: str | None = None
    created_at: str
    updated_at: str
    device_platform: str | None = None
    device_model: str | None = None
    device_system_version: str | None = None
    device_app_version: str | None = None


class AccountList(BaseModel):
    accounts: list[AccountRead]


class AccountFilter(BaseModel):
    query: str = ""
    status: AccountStatus | Literal["all"] = "all"


class AccountCheckRequest(BaseModel):
    account_id: str = Field(min_length=1)


class AccountSummary(BaseModel):
    total: int
    alive: int
    permanent_issue: int
    temporary_issue: int
    never_checked: int


class AccountTableRow(BaseModel):
    account_id: str
    label: str
    status: str
    telegram: str
    session: str
    device: str
    last_checked: str


class AccountsTableState(BaseModel):
    rows: list[AccountTableRow]
    summary: AccountSummary
