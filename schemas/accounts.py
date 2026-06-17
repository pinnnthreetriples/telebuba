from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# account_id is later joined into dialogue pair_keys via "|". Restricting the
# charset here is cheaper than escaping every join site downstream. Allows
# digit-only Telegram user_ids and the session-name stems we actually use.
_ACCOUNT_ID_PATTERN = r"^[A-Za-z0-9._-]+$"

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
    account_id: str = Field(min_length=1, pattern=_ACCOUNT_ID_PATTERN)
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
    bio: str | None = None
    proxy_type: str | None = None
    proxy_host: str | None = None
    proxy_port: int | None = None
    proxy_status: str | None = None
    proxy_last_checked_at: str | None = None
    proxy_last_error: str | None = None
    proxy_exit_ip: str | None = None
    proxy_country_code: str | None = None
    proxy_country_name: str | None = None


class AccountList(BaseModel):
    accounts: list[AccountRead]


class AccountFilter(BaseModel):
    query: str = ""
    status: AccountStatus | Literal["all"] = "all"
    # Optional pagination. ``limit=None`` returns every match (legacy default).
    limit: int | None = Field(default=None, ge=1)
    offset: int = Field(default=0, ge=0)


class AccountCheckRequest(BaseModel):
    account_id: str = Field(min_length=1, pattern=_ACCOUNT_ID_PATTERN)


class AccountProfileUpdateRequest(BaseModel):
    account_id: str = Field(min_length=1, pattern=_ACCOUNT_ID_PATTERN)
    first_name: str = Field(min_length=1)
    last_name: str | None = None
    username: str | None = None
    bio: str | None = None


class AccountSummary(BaseModel):
    total: int
    alive: int
    permanent_issue: int
    temporary_issue: int
    never_checked: int


AccountHealth = Literal["ok", "warn", "fail"]

_PERMANENT_STATUSES: frozenset[AccountStatus] = frozenset(
    {"unauthorized", "session_error", "account_error"},
)


def health_for_status(status: AccountStatus) -> AccountHealth:
    """Map an ``AccountStatus`` to a coarse traffic-light health value.

    - ``ok`` — alive (green).
    - ``fail`` — permanent: unauthorized, session_error, account_error (red).
    - ``warn`` — everything else: new + temporary issues (amber).
    """
    if status == "alive":
        return "ok"
    if status in _PERMANENT_STATUSES:
        return "fail"
    return "warn"


class AccountTableRow(BaseModel):
    account_id: str
    label: str
    status: str
    health: AccountHealth
    telegram: str
    session: str
    device: str
    proxy: str
    last_checked: str
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    bio: str | None = None
    proxy_type: str | None = None
    proxy_host: str | None = None
    proxy_port: int | None = None
    proxy_status: str | None = None
    proxy_last_checked_at: str | None = None
    proxy_last_error: str | None = None
    proxy_exit_ip: str | None = None
    proxy_country_code: str | None = None
    proxy_country_name: str | None = None


class AccountsTableState(BaseModel):
    rows: list[AccountTableRow]
    summary: AccountSummary
