"""Accounts repository (split out of core.db for #38).

Owns reads/writes of the ``accounts`` table. The proxy a account uses lives in
the shared pool (``proxies`` table, ``core.repositories.proxies``) and is joined
into the account read model via ``accounts.proxy_id``. Shared plumbing (engine,
table objects, generic row helpers) is imported from ``core.db``; the public
async functions are re-exported by ``core.db`` so existing call sites are
unaffected.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError

from core.db import (
    _accounts,
    _device_fingerprints,
    _get_engine,
    _now_iso,
    _optional_int,
    _optional_str,
    _proxies,
)

# The account cascade-delete lives in a sibling module for the file-size budget;
# re-imported here so ``core.db`` re-exports ``delete_account`` and the private
# ``_delete_account`` (used directly by tests) stays importable from this path.
from core.repositories._accounts_delete import _delete_account, delete_account  # noqa: F401
from schemas.accounts import (
    AccountCreate,
    AccountList,
    AccountProfileUpdateRequest,
    AccountRead,
    AccountStatus,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.sql import Select

    from schemas.telegram_session import TelegramSessionCheckResult

_MASK_PASSTHROUGH_LENGTH = 2


class DuplicateSessionNameError(ValueError):
    """Two accounts cannot share one Telethon session file (F5)."""


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
        proxy_id=_optional_str(mapping.get("proxy_id")),
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
        _accounts.c.proxy_id,
        _device_fingerprints.c.platform.label("device_platform"),
        _device_fingerprints.c.device_model.label("device_model"),
        _device_fingerprints.c.system_version.label("device_system_version"),
        _device_fingerprints.c.app_version.label("device_app_version"),
        _proxies.c.proxy_type.label("proxy_type"),
        _proxies.c.host.label("proxy_host"),
        _proxies.c.port.label("proxy_port"),
        _proxies.c.status.label("proxy_status"),
        _proxies.c.last_checked_at.label("proxy_last_checked_at"),
        _proxies.c.last_error.label("proxy_last_error"),
        _proxies.c.exit_ip.label("proxy_exit_ip"),
        _proxies.c.country_code.label("proxy_country_code"),
        _proxies.c.country_name.label("proxy_country_name"),
    ).select_from(
        _accounts.outerjoin(
            _device_fingerprints,
            _accounts.c.account_id == _device_fingerprints.c.account_id,
        ).outerjoin(
            _proxies,
            _accounts.c.proxy_id == _proxies.c.id,
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
        "phone": data.phone,
        "status": "new",
        "created_at": now,
        "updated_at": now,
    }
    with _get_engine().begin() as connection:
        # F5: reject a different account claiming the same Telethon session
        # file. The pre-check below catches the cooperative case; the FK
        # unique index (migration #7) is the last line of defense for racy
        # concurrent inserts.
        if data.session_name is not None:
            conflict = connection.execute(
                select(_accounts.c.account_id).where(
                    (_accounts.c.session_name == data.session_name)
                    & (_accounts.c.account_id != data.account_id),
                ),
            ).first()
            if conflict is not None:
                msg = (
                    f"Session name {data.session_name!r} is already used by account {conflict[0]!r}"
                )
                raise DuplicateSessionNameError(msg)
        try:
            connection.execute(insert(_accounts).values(**values))
        except IntegrityError:
            # P2.5: don't swallow IntegrityError blindly. Two outcomes possible:
            # (a) PK conflict on account_id  → idempotent create (existing row
            #     with same id is fine; fall through to the readback below).
            # (b) UNIQUE conflict on session_name (the migration-#7 index won
            #     a race with our pre-check) → surface the typed domain error
            #     so callers can render a useful message.
            session_owner: object | None = None
            if data.session_name is not None:
                row = connection.execute(
                    select(_accounts.c.account_id).where(
                        (_accounts.c.session_name == data.session_name)
                        & (_accounts.c.account_id != data.account_id),
                    ),
                ).first()
                session_owner = row[0] if row is not None else None
            if session_owner is not None:
                msg = (
                    f"Session name {data.session_name!r} is already used by account "
                    f"{session_owner!r}"
                )
                raise DuplicateSessionNameError(msg) from None
            # Neither a session_name collision nor a known existing account_id:
            # something else is wrong (e.g. a FK), re-raise so the operator
            # sees the real error instead of a misleading RuntimeError later.
            existing = connection.execute(
                select(_accounts.c.account_id).where(_accounts.c.account_id == data.account_id),
            ).first()
            if existing is None:
                raise
    account = _fetch_account(data.account_id)
    if account is None:
        msg = f"Account was not persisted: {data.account_id}"
        raise RuntimeError(msg)
    return account


async def create_account(data: AccountCreate) -> AccountRead:
    return await asyncio.to_thread(_create_account, data)


def _list_accounts(
    *,
    query: str = "",
    status: str = "all",
    limit: int | None = None,
    offset: int = 0,
) -> AccountList:
    statement = _account_select_statement().order_by(_accounts.c.created_at.desc())
    statement = _apply_account_filters(statement, query=query, status=status)
    if limit is not None:
        statement = statement.limit(limit).offset(offset)
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return AccountList(
        accounts=[_row_to_account(cast("Mapping[str, object]", row)) for row in rows],
    )


def _apply_account_filters(statement: Select, *, query: str, status: str) -> Select:
    if status != "all":
        statement = statement.where(_accounts.c.status == status)
    if query:
        needle = f"%{query.lower()}%"
        # Mirrors the in-memory _matches_filter haystack: account_id + label +
        # phone + username + first/last name + session_name. SQLite LIKE is
        # case-insensitive for ASCII; lower() on the column handles the rest.
        haystack = func.lower(
            func.coalesce(_accounts.c.account_id, "")
            + " "
            + func.coalesce(_accounts.c.label, "")
            + " "
            + func.coalesce(_accounts.c.phone, "")
            + " "
            + func.coalesce(_accounts.c.username, "")
            + " "
            + func.coalesce(_accounts.c.first_name, "")
            + " "
            + func.coalesce(_accounts.c.last_name, "")
            + " "
            + func.coalesce(_accounts.c.session_name, ""),
        )
        statement = statement.where(haystack.like(needle))
    return statement


async def list_accounts(
    *,
    query: str = "",
    status: str = "all",
    limit: int | None = None,
    offset: int = 0,
) -> AccountList:
    return await asyncio.to_thread(
        _list_accounts,
        query=query,
        status=status,
        limit=limit,
        offset=offset,
    )


def _account_summary_counts() -> dict[str, int]:
    statement = select(_accounts.c.status, func.count()).group_by(_accounts.c.status)
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).all()
    return {str(row[0]): int(row[1]) for row in rows}


async def account_summary_counts() -> dict[str, int]:
    """Return a status -> count mapping over the entire accounts table."""
    return await asyncio.to_thread(_account_summary_counts)


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
