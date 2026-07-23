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
    list_account_ids_for_proxy,
    list_proxies,
    unassign_account_from_proxy,
    update_proxy_check,
)
from core.logging import log_event
from core.proxy_check import check_proxy_connectivity
from core.telegram_client import evict_client
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
    # Re-adding an existing endpoint rotates its credentials on the same id; evict
    # any pooled clients so they rebuild with the new creds. A fresh insert has no
    # accounts assigned yet, so this list is empty and no eviction happens.
    for account_id in await list_account_ids_for_proxy(proxy.id):
        await evict_client(account_id)
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
    # Rebuild the pooled client with the new proxy on next use (no-op if none cached).
    await evict_client(account_id)
    await log_event("INFO", "proxy_assigned", account_id=account_id, extra={"proxy_id": proxy_id})
    return proxy


async def unassign_proxy(account_id: str) -> None:
    await unassign_account_from_proxy(account_id)
    # Rebuild the pooled client without a proxy (direct) on next use.
    await evict_client(account_id)
    await log_event("INFO", "proxy_unassigned", account_id=account_id)


async def remove_proxy(proxy_id: str) -> None:
    # Capture the accounts on this proxy before the delete nulls their proxy_id,
    # then evict their pooled clients so they stop tunnelling through the dropped
    # endpoint (they rebuild direct on next use).
    account_ids = await list_account_ids_for_proxy(proxy_id)
    await delete_proxy(proxy_id)
    for account_id in account_ids:
        await evict_client(account_id)
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
            geo_status=result.geo_status,
            ipinfo_country_code=result.ipinfo_country_code,
            maxmind_country_code=result.maxmind_country_code,
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
            "geo_status": saved.geo_status,
            "ipinfo_country_code": saved.ipinfo_country_code,
            "maxmind_country_code": saved.maxmind_country_code,
            "last_error": saved.last_error,
        },
    )
    return saved
