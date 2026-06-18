"""Proxy attach / detach / connectivity-check for the accounts domain.

``check_proxy_connectivity`` is imported at module scope so tests can monkeypatch
``services.accounts.proxy.check_proxy_connectivity``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.db import (
    delete_account_proxy as delete_account_proxy_row,
)
from core.db import (
    fetch_account_proxy_settings,
    update_account_proxy_check,
    upsert_account_proxy,
)
from core.logging import log_event
from core.proxy_check import check_proxy_connectivity
from schemas.proxy import AccountProxyCheckUpdate

if TYPE_CHECKING:
    from schemas.proxy import (
        AccountProxyCheckRequest,
        AccountProxyDelete,
        AccountProxyRead,
        AccountProxyUpsert,
    )

__all__ = [
    "check_account_proxy",
    "delete_account_proxy",
    "fetch_account_proxy_settings",
    "save_account_proxy",
]


async def save_account_proxy(data: AccountProxyUpsert) -> AccountProxyRead:
    proxy = await upsert_account_proxy(data)
    await log_event(
        "INFO",
        "account_proxy_saved",
        account_id=data.account_id,
        extra={
            "proxy_type": proxy.proxy_type,
            "host": proxy.host,
            "port": proxy.port,
            "has_username": proxy.username is not None,
            "has_password": proxy.has_password,
        },
    )
    return proxy


async def delete_account_proxy(data: AccountProxyDelete) -> None:
    await delete_account_proxy_row(data)
    await log_event("INFO", "account_proxy_deleted", account_id=data.account_id)


async def check_account_proxy(data: AccountProxyCheckRequest) -> AccountProxyRead:
    proxy = await fetch_account_proxy_settings(data.account_id)
    if proxy is None:
        msg = f"Proxy not found for account: {data.account_id}"
        raise ValueError(msg)
    result = await check_proxy_connectivity(proxy)
    saved = await update_account_proxy_check(
        AccountProxyCheckUpdate(
            account_id=data.account_id,
            status=result.status,
            last_error=result.last_error,
            exit_ip=result.exit_ip,
            country_code=result.country_code,
            country_name=result.country_name,
            asn=result.asn,
            is_datacenter=result.is_datacenter,
        ),
    )
    await log_event(
        "INFO" if saved.status == "tcp_working" else "WARNING",
        "account_proxy_checked",
        account_id=data.account_id,
        extra={
            "status": saved.status,
            "exit_ip": saved.exit_ip,
            "country_code": saved.country_code,
            "country_name": saved.country_name,
            "last_error": saved.last_error,
        },
    )
    return saved
