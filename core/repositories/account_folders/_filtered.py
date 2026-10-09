"""The Accounts page's list under its SQL-side filters: folder, status set, proxy country.

Built on the accounts read model (``core.repositories.accounts``) so a filtered row is
the same ``AccountRead`` an unfiltered one is. Filters that need Python (phone calling
code, Trust) are the service's.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import func, select

from core.db import _accounts, _get_engine, _proxies
from core.repositories.account_folders._tables import _account_folder_members as _members
from core.repositories.accounts import (
    _account_select_statement,
    _apply_account_filters,
    _row_to_account,
)
from schemas.account_folders import NO_PROXY, UNFILED, AccountListFilters
from schemas.accounts import AccountList

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.sql import Select


def _filtered(statement: Select[tuple[Any, ...]], filters: AccountListFilters) -> Select:
    statement = _apply_account_filters(statement, query=filters.query, status="all")
    if filters.statuses is not None:
        statement = statement.where(_accounts.c.status.in_(sorted(filters.statuses)))
    if filters.folder == UNFILED:
        statement = statement.where(_accounts.c.account_id.not_in(select(_members.c.account_id)))
    elif filters.folder is not None:
        statement = statement.where(
            _accounts.c.account_id.in_(
                select(_members.c.account_id).where(_members.c.folder_id == filters.folder)
            )
        )
    if filters.proxy_country == NO_PROXY:
        statement = statement.where(_accounts.c.proxy_id.is_(None))
    elif filters.proxy_country is not None:
        statement = statement.where(func.upper(_proxies.c.country_code) == filters.proxy_country)
    return statement


def _list_filtered_accounts(
    filters: AccountListFilters, limit: int | None, offset: int
) -> AccountList:
    statement = _filtered(_account_select_statement(), filters).order_by(
        _accounts.c.created_at.desc()
    )
    if limit is not None:
        statement = statement.limit(limit).offset(offset)
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return AccountList(
        accounts=[_row_to_account(cast("Mapping[str, object]", row)) for row in rows],
    )


async def list_filtered_accounts(
    filters: AccountListFilters, *, limit: int | None = None, offset: int = 0
) -> AccountList:
    """Newest first, like the unfiltered list; ``limit=None`` reads every match."""
    return await asyncio.to_thread(_list_filtered_accounts, filters, limit, offset)


def _count_filtered_accounts(filters: AccountListFilters) -> int:
    matches = _filtered(_account_select_statement(), filters).subquery()
    with _get_engine().connect() as connection:
        return int(connection.execute(select(func.count()).select_from(matches)).scalar_one())


async def count_filtered_accounts(filters: AccountListFilters) -> int:
    return await asyncio.to_thread(_count_filtered_accounts, filters)
