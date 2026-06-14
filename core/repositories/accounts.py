"""Accounts + per-account proxies repository (split out of core.db for #38).

Owns reads/writes of the ``accounts`` and ``account_proxies`` tables (one
aggregate — proxies have a FK to accounts and are masked into the account read
model). Shared plumbing (engine, table objects, generic row helpers) is imported
from ``core.db``; the public async functions are re-exported by ``core.db`` so
existing call sites are unaffected.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from core.db import (
    _account_proxies,
    _accounts,
    _device_fingerprints,
    _get_engine,
    _now_iso,
    _optional_int,
    _optional_str,
    _required_int,
)
from schemas.accounts import (
    AccountCreate,
    AccountList,
    AccountProfileUpdateRequest,
    AccountRead,
    AccountStatus,
)
from schemas.proxy import (
    AccountProxyCheckUpdate,
    AccountProxyDelete,
    AccountProxyRead,
    AccountProxySettings,
    AccountProxyUpsert,
    ProxyStatus,
    ProxyType,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.sql import Select

    from schemas.telegram_session import TelegramSessionCheckResult

_MASK_PASSTHROUGH_LENGTH = 2


def _row_to_account(mapping: Mapping[str, object]) -> AccountRead:
    return AccountRead(
        account_id=str(mapping["account_id"]),
        label=_optional_str(mapping.get("label")),
        session_name=_optional_str(mapping.get("session_name")),
        status=cast("AccountStatus", mapping["status"]),
        user_id=_optional_int(mapping.get("user_id")),
        phone=_optional_str(mapping.get("phone")),
        username=_optional_str(mapping.get("username")),
        first_name=_optional_str(mapping.get("first_name")),
        last_name=_optional_str(mapping.get("last_name")),
        bio=_optional_str(mapping.get("bio")),
        last_checked_at=_optional_str(mapping.get("last_checked_at")),
        created_at=str(mapping["created_at"]),
        updated_at=str(mapping["updated_at"]),
        device_platform=_optional_str(mapping.get("device_platform")),
        device_model=_optional_str(mapping.get("device_model")),
        device_system_version=_optional_str(mapping.get("device_system_version")),
        device_app_version=_optional_str(mapping.get("device_app_version")),
        proxy_type=_optional_str(mapping.get("proxy_type")),
        proxy_host=_optional_str(mapping.get("proxy_host")),
        proxy_port=_optional_int(mapping.get("proxy_port")),
        proxy_status=_optional_str(mapping.get("proxy_status")),
        proxy_last_checked_at=_optional_str(mapping.get("proxy_last_checked_at")),
        proxy_last_error=_optional_str(mapping.get("proxy_last_error")),
        proxy_exit_ip=_optional_str(mapping.get("proxy_exit_ip")),
        proxy_country_code=_optional_str(mapping.get("proxy_country_code")),
        proxy_country_name=_optional_str(mapping.get("proxy_country_name")),
    )


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


def _account_select_statement() -> Select[tuple[Any, ...]]:
    return select(
        _accounts.c.account_id,
        _accounts.c.label,
        _accounts.c.session_name,
        _accounts.c.status,
        _accounts.c.user_id,
        _accounts.c.phone,
        _accounts.c.username,
        _accounts.c.first_name,
        _accounts.c.last_name,
        _accounts.c.bio,
        _accounts.c.last_checked_at,
        _accounts.c.created_at,
        _accounts.c.updated_at,
        _device_fingerprints.c.platform.label("device_platform"),
        _device_fingerprints.c.device_model.label("device_model"),
        _device_fingerprints.c.system_version.label("device_system_version"),
        _device_fingerprints.c.app_version.label("device_app_version"),
        _account_proxies.c.proxy_type.label("proxy_type"),
        _account_proxies.c.host.label("proxy_host"),
        _account_proxies.c.port.label("proxy_port"),
        _account_proxies.c.status.label("proxy_status"),
        _account_proxies.c.last_checked_at.label("proxy_last_checked_at"),
        _account_proxies.c.last_error.label("proxy_last_error"),
        _account_proxies.c.exit_ip.label("proxy_exit_ip"),
        _account_proxies.c.country_code.label("proxy_country_code"),
        _account_proxies.c.country_name.label("proxy_country_name"),
    ).select_from(
        _accounts.outerjoin(
            _device_fingerprints,
            _accounts.c.account_id == _device_fingerprints.c.account_id,
        ).outerjoin(
            _account_proxies,
            _accounts.c.account_id == _account_proxies.c.account_id,
        ),
    )


def _fetch_account(account_id: str) -> AccountRead | None:
    statement = _account_select_statement().where(_accounts.c.account_id == account_id)
    with _get_engine().connect() as connection:
        row = connection.execute(statement).mappings().first()
    if row is None:
        return None
    return _row_to_account(cast("Mapping[str, object]", row))


async def fetch_account(account_id: str) -> AccountRead | None:
    return await asyncio.to_thread(_fetch_account, account_id)


def _create_account(data: AccountCreate) -> AccountRead:
    now = _now_iso()
    values = {
        "account_id": data.account_id,
        "label": data.label,
        "session_name": data.session_name,
        "status": "new",
        "created_at": now,
        "updated_at": now,
    }
    with _get_engine().begin() as connection, suppress(IntegrityError):
        connection.execute(insert(_accounts).values(**values))
    account = _fetch_account(data.account_id)
    if account is None:
        msg = f"Account was not persisted: {data.account_id}"
        raise RuntimeError(msg)
    return account


async def create_account(data: AccountCreate) -> AccountRead:
    return await asyncio.to_thread(_create_account, data)


def _list_accounts() -> AccountList:
    statement = _account_select_statement().order_by(_accounts.c.created_at.desc())
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return AccountList(
        accounts=[_row_to_account(cast("Mapping[str, object]", row)) for row in rows],
    )


async def list_accounts() -> AccountList:
    return await asyncio.to_thread(_list_accounts)


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


def _update_account_profile_snapshot(data: AccountProfileUpdateRequest) -> AccountRead:
    values: dict[str, object | None] = {
        "first_name": data.first_name,
        "updated_at": _now_iso(),
    }
    if data.last_name is not None:
        values["last_name"] = data.last_name
    if data.username is not None:
        values["username"] = data.username
    if data.bio is not None:
        values["bio"] = data.bio
    with _get_engine().begin() as connection:
        result = connection.execute(
            update(_accounts).where(_accounts.c.account_id == data.account_id).values(**values),
        )
    if result.rowcount == 0:
        msg = f"Account not found: {data.account_id}"
        raise ValueError(msg)
    account = _fetch_account(data.account_id)
    if account is None:
        msg = f"Account not found: {data.account_id}"
        raise ValueError(msg)
    return account


async def update_account_profile_snapshot(data: AccountProfileUpdateRequest) -> AccountRead:
    return await asyncio.to_thread(_update_account_profile_snapshot, data)


def _update_account_from_session_check(result: TelegramSessionCheckResult) -> AccountRead:
    now = _now_iso()
    values: dict[str, object] = {
        "status": result.status,
        "last_checked_at": now,
        "updated_at": now,
    }
    if result.status == "alive":
        values.update(
            {
                "user_id": result.user_id,
                "phone": result.phone,
                "username": result.username,
                "first_name": result.first_name,
                "last_name": result.last_name,
            },
        )

    with _get_engine().begin() as connection:
        connection.execute(
            update(_accounts).where(_accounts.c.account_id == result.account_id).values(**values),
        )

    account = _fetch_account(result.account_id)
    if account is None:
        msg = f"Account not found: {result.account_id}"
        raise RuntimeError(msg)
    return account


async def update_account_from_session_check(result: TelegramSessionCheckResult) -> AccountRead:
    return await asyncio.to_thread(_update_account_from_session_check, result)
