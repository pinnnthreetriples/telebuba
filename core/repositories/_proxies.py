"""Account-proxy persistence — extracted from ``core.repositories.accounts``.

The proxy CRUD lives next to the same table but is split off so the parent
module stays under the aislop file-size gate. Public API stays re-exported
via ``core.db``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from sqlalchemy import delete, insert, select, update

from core.db import _account_proxies, _get_engine, _now_iso, _optional_str, _required_int

if TYPE_CHECKING:
    from collections.abc import Mapping

from schemas.proxy import (
    AccountProxyCheckUpdate,
    AccountProxyDelete,
    AccountProxyRead,
    AccountProxySettings,
    AccountProxyUpsert,
    ProxyStatus,
    ProxyType,
)

_MASK_PASSTHROUGH_LENGTH = 2


def _mask_username(username: str | None) -> str | None:
    if not username:
        return None
    if len(username) <= _MASK_PASSTHROUGH_LENGTH:
        return f"{username[0]}*"
    return f"{username[0]}***{username[-1]}"


def _row_to_account_proxy(mapping: Mapping[str, object]) -> AccountProxyRead:
    return AccountProxyRead(
        account_id=str(mapping["account_id"]),
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
        asn=_optional_str(mapping.get("asn")),
        is_datacenter=bool(mapping.get("is_datacenter")),
        updated_at=str(mapping["updated_at"]),
    )


def _row_to_account_proxy_settings(mapping: Mapping[str, object]) -> AccountProxySettings:
    return AccountProxySettings(
        account_id=str(mapping["account_id"]),
        proxy_type=cast("ProxyType", mapping["proxy_type"]),
        host=str(mapping["host"]),
        port=_required_int(mapping["port"]),
        username=_optional_str(mapping.get("username")),
        password=_optional_str(mapping.get("password")),
    )


def _fetch_account_proxy(account_id: str) -> AccountProxyRead | None:
    statement = select(_account_proxies).where(_account_proxies.c.account_id == account_id)
    with _get_engine().begin() as connection:
        row = connection.execute(statement).mappings().first()
    if row is None:
        return None
    return _row_to_account_proxy(cast("Mapping[str, object]", row))


async def fetch_account_proxy(account_id: str) -> AccountProxyRead | None:
    return await asyncio.to_thread(_fetch_account_proxy, account_id)


def _fetch_account_proxy_settings(account_id: str) -> AccountProxySettings | None:
    statement = select(_account_proxies).where(_account_proxies.c.account_id == account_id)
    with _get_engine().begin() as connection:
        row = connection.execute(statement).mappings().first()
    if row is None:
        return None
    return _row_to_account_proxy_settings(cast("Mapping[str, object]", row))


async def fetch_account_proxy_settings(account_id: str) -> AccountProxySettings | None:
    return await asyncio.to_thread(_fetch_account_proxy_settings, account_id)


def _upsert_account_proxy(data: AccountProxyUpsert) -> AccountProxyRead:

    from core.repositories.accounts import _fetch_account  # noqa: PLC0415

    if _fetch_account(data.account_id) is None:
        msg = f"Account not found: {data.account_id}"
        raise ValueError(msg)
    now = _now_iso()
    values: dict[str, object | None] = {
        "proxy_type": data.proxy_type,
        "host": data.host.strip(),
        "port": data.port,
        "username": data.username.strip() if data.username else None,
        "status": "unknown",
        "last_checked_at": None,
        "last_error": None,
        "exit_ip": None,
        "country_code": None,
        "country_name": None,
        "updated_at": now,
    }
    existing = _fetch_account_proxy_settings(data.account_id)
    with _get_engine().begin() as connection:
        if existing is None:
            connection.execute(
                insert(_account_proxies).values(
                    account_id=data.account_id,
                    password=data.password,
                    created_at=now,
                    **values,
                ),
            )
        else:
            if data.password is not None:
                values["password"] = data.password
            connection.execute(
                update(_account_proxies)
                .where(_account_proxies.c.account_id == data.account_id)
                .values(**values),
            )
    proxy = _fetch_account_proxy(data.account_id)
    if proxy is None:
        msg = f"Proxy was not persisted: {data.account_id}"
        raise RuntimeError(msg)
    return proxy


async def upsert_account_proxy(data: AccountProxyUpsert) -> AccountProxyRead:
    return await asyncio.to_thread(_upsert_account_proxy, data)


def _delete_account_proxy(data: AccountProxyDelete) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            delete(_account_proxies).where(_account_proxies.c.account_id == data.account_id),
        )


async def delete_account_proxy(data: AccountProxyDelete) -> None:
    await asyncio.to_thread(_delete_account_proxy, data)


def _exit_ip_collisions() -> dict[str, list[str]]:
    statement = select(
        _account_proxies.c.account_id,
        _account_proxies.c.exit_ip,
    ).where(_account_proxies.c.exit_ip.is_not(None))
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).all()
    grouped: dict[str, list[str]] = {}
    for account_id, exit_ip in rows:
        grouped.setdefault(str(exit_ip), []).append(str(account_id))
    return {ip: ids for ip, ids in grouped.items() if len(ids) > 1}


async def exit_ip_collisions() -> dict[str, list[str]]:
    """Map each shared exit IP to the accounts using it (only IPs used by 2+)."""
    return await asyncio.to_thread(_exit_ip_collisions)


def _update_account_proxy_check(data: AccountProxyCheckUpdate) -> AccountProxyRead:
    now = _now_iso()
    values: dict[str, object | None] = {
        "status": data.status,
        "last_checked_at": now,
        "last_error": data.last_error,
        "exit_ip": data.exit_ip,
        "country_code": data.country_code,
        "country_name": data.country_name,
        "asn": data.asn,
        "is_datacenter": int(data.is_datacenter),
        "updated_at": now,
    }
    with _get_engine().begin() as connection:
        result = connection.execute(
            update(_account_proxies)
            .where(_account_proxies.c.account_id == data.account_id)
            .values(**values),
        )
    if result.rowcount == 0:
        msg = f"Proxy not found for account: {data.account_id}"
        raise ValueError(msg)
    proxy = _fetch_account_proxy(data.account_id)
    if proxy is None:
        msg = f"Proxy not found for account: {data.account_id}"
        raise ValueError(msg)
    return proxy


async def update_account_proxy_check(data: AccountProxyCheckUpdate) -> AccountProxyRead:
    return await asyncio.to_thread(_update_account_proxy_check, data)
