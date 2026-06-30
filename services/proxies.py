"""Proxy-pool business logic — add / list / assign / check shared pool proxies.

UI-agnostic. Talks to the ``core`` gateways (the pool DB repo + the connectivity
probe); no SQLAlchemy / Telethon here. ``check_proxy_connectivity`` is imported
at module scope so tests can monkeypatch ``services.proxies.check_proxy_connectivity``.
"""

from __future__ import annotations

from core.db import (
    ProxyCapacityError,
    assign_account_to_proxy,
    create_proxy,
    delete_proxy,
    fetch_proxy_settings,
    list_proxies,
    unassign_account_from_proxy,
    update_proxy_check,
)
from core.logging import log_event
from core.proxy_check import check_proxy_connectivity
from schemas.proxy import (
    ProxyCheckResult,
    ProxyCheckUpdate,
    ProxyCreate,
    ProxyList,
    ProxyRead,
    ProxySettings,
)

__all__ = [
    "ProxyCapacityError",
    "add_proxy",
    "assign_proxy",
    "check_proxy",
    "list_pool",
    "probe_proxy",
    "remove_proxy",
    "unassign_proxy",
]


async def list_pool() -> ProxyList:
    return await list_proxies()


async def probe_proxy(data: ProxyCreate) -> ProxyCheckResult:
    """Stateless connectivity probe for not-yet-saved proxy settings (the add form)."""
    return await check_proxy_connectivity(
        ProxySettings(
            proxy_type=data.proxy_type,
            host=data.host,
            port=data.port,
            username=data.username,
            password=data.password,
        ),
    )


async def add_proxy(data: ProxyCreate) -> ProxyRead:
    proxy = await create_proxy(data)
    await log_event(
        "INFO",
        "proxy_added",
        extra={
            "proxy_id": proxy.id,
            "host": proxy.host,
            "port": proxy.port,
            "proxy_type": proxy.proxy_type,
        },
    )
    return proxy


async def assign_proxy(proxy_id: str, account_id: str) -> ProxyRead:
    proxy = await assign_account_to_proxy(proxy_id, account_id)
    await log_event("INFO", "proxy_assigned", account_id=account_id, extra={"proxy_id": proxy_id})
    return proxy


async def unassign_proxy(account_id: str) -> None:
    await unassign_account_from_proxy(account_id)
    await log_event("INFO", "proxy_unassigned", account_id=account_id)


async def remove_proxy(proxy_id: str) -> None:
    await delete_proxy(proxy_id)
    await log_event("INFO", "proxy_removed", extra={"proxy_id": proxy_id})


async def check_proxy(proxy_id: str) -> ProxyRead:
    proxy_settings = await fetch_proxy_settings(proxy_id)
    if proxy_settings is None:
        msg = f"Proxy not found: {proxy_id}"
        raise ValueError(msg)
    result = await check_proxy_connectivity(proxy_settings)
    saved = await update_proxy_check(
        ProxyCheckUpdate(
            proxy_id=proxy_id,
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
        "proxy_checked",
        extra={
            "proxy_id": proxy_id,
            "status": saved.status,
            "exit_ip": saved.exit_ip,
            "country_code": saved.country_code,
            "last_error": saved.last_error,
        },
    )
    return saved
