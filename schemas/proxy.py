from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ProxyType = Literal["socks5", "http"]
ProxyStatus = Literal["unknown", "tcp_working", "failed"]


class AccountProxyUpsert(BaseModel):
    account_id: str = Field(min_length=1)
    proxy_type: ProxyType
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65_535)
    username: str | None = Field(default=None, min_length=1)
    password: str | None = Field(default=None, min_length=1)


class AccountProxyDelete(BaseModel):
    account_id: str = Field(min_length=1)


class AccountProxyCheckRequest(BaseModel):
    account_id: str = Field(min_length=1)


class AccountProxyRead(BaseModel):
    account_id: str = Field(min_length=1)
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
    updated_at: str


class AccountProxySettings(BaseModel):
    account_id: str = Field(min_length=1)
    proxy_type: ProxyType
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65_535)
    username: str | None = None
    password: str | None = None


class ProxyCheckResult(BaseModel):
    status: ProxyStatus
    last_error: str | None = None
    exit_ip: str | None = None
    country_code: str | None = None
    country_name: str | None = None


class AccountProxyCheckUpdate(BaseModel):
    account_id: str = Field(min_length=1)
    status: ProxyStatus
    last_error: str | None = None
    exit_ip: str | None = None
    country_code: str | None = None
    country_name: str | None = None
