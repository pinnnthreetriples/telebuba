"""Proxy-pool persistence — the single store for all proxies.

Replaces #38's per-account ``_account_proxies``. A proxy is a first-class row;
accounts reference one via ``accounts.proxy_id`` (many accounts → one proxy,
capacity ``settings.proxy.max_accounts_per_proxy``). Public API is re-exported
from ``core.db`` so the Telegram gateway's ``fetch_account_proxy_settings``
seam keeps working unchanged.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, cast

from sqlalchemy import delete, func, insert, select, update

from core.config import settings
from core.db import _accounts, _get_engine, _now_iso, _optional_str, _proxies, _required_int
from schemas.proxy import (
    GeoStatus,
    ProxyCheckUpdate,
    ProxyCreate,
    ProxyList,
    ProxyRead,
    ProxySettings,
    ProxyStatus,
    ProxyType,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.engine import Connection

_MASK_PASSTHROUGH_LENGTH = 2


class ProxyCapacityError(ValueError):
    """A proxy is already serving its maximum number of accounts."""


def _capacity() -> int:
    return settings.proxy.max_accounts_per_proxy


def _mask_username(username: str | None) -> str | None:
    if not username:
        return None
    if len(username) <= _MASK_PASSTHROUGH_LENGTH:
        return f"{username[0]}*"
    return f"{username[0]}***{username[-1]}"


def _row_to_proxy(mapping: Mapping[str, object], used: int) -> ProxyRead:
    capacity = _capacity()
    return ProxyRead(
        id=str(mapping["id"]),
        proxy_type=cast("ProxyType", mapping["proxy_type"]),
        host=str(mapping["host"]),
        port=_required_int(mapping["port"]),
        username=_mask_username(_optional_str(mapping.get("username"))),
        has_password=bool(mapping.get("password")),
        status=cast("ProxyStatus", mapping["status"]),
        last_checked_at=_optional_str(mapping.get("last_checked_at")),
        last_error=_optional_str(mapping.get("last_error")),
        exit_ip=_optional_str(mapping.get("exit_ip")),
        country_code=_optional_str(mapping.get("country_code")),
        country_name=_optional_str(mapping.get("country_name")),
        geo_status=cast("GeoStatus", mapping.get("geo_status") or "unknown"),
        ipinfo_country_code=_optional_str(mapping.get("ipinfo_country_code")),
        maxmind_country_code=_optional_str(mapping.get("maxmind_country_code")),
        asn=_optional_str(mapping.get("asn")),
        is_datacenter=bool(mapping.get("is_datacenter")),
        created_at=str(mapping["created_at"]),
        updated_at=str(mapping["updated_at"]),
        used=used,
        capacity=capacity,
        free=max(0, capacity - used),
    )


def _row_to_settings(mapping: Mapping[str, object]) -> ProxySettings:
    return ProxySettings(
        proxy_type=cast("ProxyType", mapping["proxy_type"]),
        host=str(mapping["host"]),
        port=_required_int(mapping["port"]),
        username=_optional_str(mapping.get("username")),
        password=_optional_str(mapping.get("password")),
    )


def _used_counts(connection: Connection) -> dict[str, int]:
    statement = (
        select(_accounts.c.proxy_id, func.count().label("n"))
        .where(_accounts.c.proxy_id.is_not(None))
        .group_by(_accounts.c.proxy_id)
    )
    rows = connection.execute(statement).all()
    return {str(proxy_id): int(count) for proxy_id, count in rows}


def _count_for_proxy(connection: Connection, proxy_id: str) -> int:
    statement = select(func.count()).where(_accounts.c.proxy_id == proxy_id)
    return int(connection.execute(statement).scalar() or 0)


def _list_account_ids_for_proxy(proxy_id: str) -> list[str]:
    statement = select(_accounts.c.account_id).where(_accounts.c.proxy_id == proxy_id)
    with _get_engine().connect() as connection:
        return [str(row[0]) for row in connection.execute(statement).all()]


async def list_account_ids_for_proxy(proxy_id: str) -> list[str]:
    """Account ids currently assigned to ``proxy_id`` (for pooled-client eviction)."""
    return await asyncio.to_thread(_list_account_ids_for_proxy, proxy_id)


def _list_proxies() -> ProxyList:
    with _get_engine().connect() as connection:
        rows = connection.execute(select(_proxies)).mappings().all()
        counts = _used_counts(connection)
    proxies = [
        _row_to_proxy(cast("Mapping[str, object]", row), counts.get(str(row["id"]), 0))
        for row in rows
    ]
    return ProxyList(proxies=proxies)


async def list_proxies() -> ProxyList:
    return await asyncio.to_thread(_list_proxies)


def _fetch_proxy(proxy_id: str) -> ProxyRead | None:
    with _get_engine().connect() as connection:
        row = (
            connection.execute(
                select(_proxies).where(_proxies.c.id == proxy_id),
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        used = _count_for_proxy(connection, proxy_id)
    return _row_to_proxy(cast("Mapping[str, object]", row), used)


async def fetch_proxy(proxy_id: str) -> ProxyRead | None:
    return await asyncio.to_thread(_fetch_proxy, proxy_id)


def _fetch_proxy_settings(proxy_id: str) -> ProxySettings | None:
    with _get_engine().connect() as connection:
        row = (
            connection.execute(
                select(_proxies).where(_proxies.c.id == proxy_id),
            )
            .mappings()
            .first()
        )
    if row is None:
        return None
    return _row_to_settings(cast("Mapping[str, object]", row))


async def fetch_proxy_settings(proxy_id: str) -> ProxySettings | None:
    return await asyncio.to_thread(_fetch_proxy_settings, proxy_id)


def _fetch_account_proxy_settings(account_id: str) -> ProxySettings | None:
    statement = (
        select(
            _proxies.c.proxy_type,
            _proxies.c.host,
            _proxies.c.port,
            _proxies.c.username,
            _proxies.c.password,
        )
        .select_from(_accounts.join(_proxies, _accounts.c.proxy_id == _proxies.c.id))
        .where(_accounts.c.account_id == account_id)
    )
    with _get_engine().connect() as connection:
        row = connection.execute(statement).mappings().first()
    if row is None:
        return None
    return _row_to_settings(cast("Mapping[str, object]", row))


async def fetch_account_proxy_settings(account_id: str) -> ProxySettings | None:
    """Resolve the proxy an account is assigned to (the Telegram-gateway seam)."""
    return await asyncio.to_thread(_fetch_account_proxy_settings, account_id)


def _create_proxy(data: ProxyCreate) -> ProxyRead:
    now = _now_iso()
    host = data.host.strip()
    username = data.username.strip() if data.username else None
    with _get_engine().begin() as connection:
        existing = connection.execute(
            select(_proxies.c.id).where(
                _proxies.c.host == host,
                _proxies.c.port == data.port,
                _proxies.c.proxy_type == data.proxy_type,
            ),
        ).first()
        if existing is None:
            proxy_id = uuid.uuid4().hex
            connection.execute(
                insert(_proxies).values(
                    id=proxy_id,
                    proxy_type=data.proxy_type,
                    host=host,
                    port=data.port,
                    username=username,
                    password=data.password,
                    status="unknown",
                    created_at=now,
                    updated_at=now,
                ),
            )
        else:
            # Same endpoint re-added — refresh credentials, keep the existing id
            # and any prior check result.
            proxy_id = str(existing[0])
            connection.execute(
                update(_proxies)
                .where(_proxies.c.id == proxy_id)
                .values(username=username, password=data.password, updated_at=now),
            )
    proxy = _fetch_proxy(proxy_id)
    if proxy is None:  # pragma: no cover - just inserted
        msg = f"Proxy was not persisted: {proxy_id}"
        raise RuntimeError(msg)
    return proxy


async def create_proxy(data: ProxyCreate) -> ProxyRead:
    return await asyncio.to_thread(_create_proxy, data)


def _assign_account_to_proxy(proxy_id: str, account_id: str) -> ProxyRead:
    with _get_engine().begin() as connection:
        if (
            connection.execute(
                select(_proxies.c.id).where(_proxies.c.id == proxy_id),
            ).first()
            is None
        ):
            msg = f"Proxy not found: {proxy_id}"
            raise ValueError(msg)
        account_row = connection.execute(
            select(_accounts.c.proxy_id).where(_accounts.c.account_id == account_id),
        ).first()
        if account_row is None:
            msg = f"Account not found: {account_id}"
            raise ValueError(msg)
        already_here = str(account_row[0]) == proxy_id if account_row[0] is not None else False
        if not already_here and _count_for_proxy(connection, proxy_id) >= _capacity():
            msg = f"Proxy {proxy_id} is at capacity ({_capacity()})"
            raise ProxyCapacityError(msg)
        connection.execute(
            update(_accounts).where(_accounts.c.account_id == account_id).values(proxy_id=proxy_id),
        )
    proxy = _fetch_proxy(proxy_id)
    if proxy is None:  # pragma: no cover - existence checked above
        msg = f"Proxy not found: {proxy_id}"
        raise ValueError(msg)
    return proxy


async def assign_account_to_proxy(proxy_id: str, account_id: str) -> ProxyRead:
    return await asyncio.to_thread(_assign_account_to_proxy, proxy_id, account_id)


def _unassign_account_from_proxy(account_id: str) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            update(_accounts).where(_accounts.c.account_id == account_id).values(proxy_id=None),
        )


async def unassign_account_from_proxy(account_id: str) -> None:
    await asyncio.to_thread(_unassign_account_from_proxy, account_id)


def _delete_proxy(proxy_id: str) -> None:
    with _get_engine().begin() as connection:
        # Detach its accounts first (they become "—"), then drop the proxy.
        connection.execute(
            update(_accounts).where(_accounts.c.proxy_id == proxy_id).values(proxy_id=None),
        )
        connection.execute(delete(_proxies).where(_proxies.c.id == proxy_id))


async def delete_proxy(proxy_id: str) -> None:
    await asyncio.to_thread(_delete_proxy, proxy_id)


def _update_proxy_check(data: ProxyCheckUpdate) -> ProxyRead:
    now = _now_iso()
    values: dict[str, object | None] = {
        "status": data.status,
        "last_checked_at": now,
        "last_error": data.last_error,
        "exit_ip": data.exit_ip,
        "country_code": data.country_code,
        "country_name": data.country_name,
        "geo_status": data.geo_status,
        "ipinfo_country_code": data.ipinfo_country_code,
        "maxmind_country_code": data.maxmind_country_code,
        "asn": data.asn,
        "is_datacenter": int(data.is_datacenter),
        "updated_at": now,
    }
    with _get_engine().begin() as connection:
        result = connection.execute(
            update(_proxies).where(_proxies.c.id == data.proxy_id).values(**values),
        )
    if result.rowcount == 0:
        msg = f"Proxy not found: {data.proxy_id}"
        raise ValueError(msg)
    proxy = _fetch_proxy(data.proxy_id)
    if proxy is None:  # pragma: no cover - updated above
        msg = f"Proxy not found: {data.proxy_id}"
        raise ValueError(msg)
    return proxy


async def update_proxy_check(data: ProxyCheckUpdate) -> ProxyRead:
    return await asyncio.to_thread(_update_proxy_check, data)
