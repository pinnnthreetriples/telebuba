from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ProxyType = Literal["socks5", "https"]
ProxyStatus = Literal["unknown", "tcp_working", "failed"]


class ProxyCreate(BaseModel):
    """Operator input when adding a proxy to the pool."""

    proxy_type: ProxyType
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65_535)
    username: str | None = Field(default=None, min_length=1)
    password: str | None = Field(default=None, min_length=1)


class ProxyRead(BaseModel):
    """A pool proxy as shown on the Accounts page (masked credentials)."""

    id: str = Field(min_length=1)
    proxy_type: ProxyType
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65_535)
    username: str | None = None
    has_password: bool
    status: ProxyStatus
    last_checked_at: str | None = None
    last_error: str | None = None
    exit_ip: str | None = None
    country_code: str | None = None
    country_name: str | None = None
    asn: str | None = None
    is_datacenter: bool = False
    created_at: str
    updated_at: str
    # Pool usage: how many accounts use this proxy vs the global capacity.
    used: int = Field(ge=0)
    capacity: int = Field(ge=1)
    free: int = Field(ge=0)


class ProxyList(BaseModel):
    proxies: list[ProxyRead]


class ProxySettings(BaseModel):
    """Unmasked proxy credentials handed to the Telegram/connectivity gateways."""

    proxy_type: ProxyType
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65_535)
    username: str | None = None
    password: str | None = None


class ProxyAssignRequest(BaseModel):
    account_id: str = Field(min_length=1)


class ProxyCheckResult(BaseModel):
    status: ProxyStatus
    last_error: str | None = None
    exit_ip: str | None = None
    country_code: str | None = None
    country_name: str | None = None
    asn: str | None = None
    is_datacenter: bool = False


class ProxyCheckUpdate(BaseModel):
    proxy_id: str = Field(min_length=1)
    status: ProxyStatus
    last_error: str | None = None
    exit_ip: str | None = None
    country_code: str | None = None
    country_name: str | None = None
    asn: str | None = None
    is_datacenter: bool = False
