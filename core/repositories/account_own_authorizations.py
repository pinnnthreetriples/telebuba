"""Authorizations Telebuba created itself (its Telegram Web login): one row per account and hash.

Sync helpers on a worker thread, the ``core.repositories.account_rekeys`` shape. They go with
the account (``core.repositories._accounts_delete``).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core._schema_own_authorizations import _account_own_authorizations
from core.db import _get_engine

if TYPE_CHECKING:
    from collections.abc import Iterable


def _save_own_authorizations(
    account_id: str, hashes: list[str], kind: str, recorded_at: str
) -> None:
    if not hashes:
        return
    rows = [
        {"account_id": account_id, "hash": hash_, "kind": kind, "recorded_at": recorded_at}
        for hash_ in hashes
    ]
    statement = sqlite_insert(_account_own_authorizations).values(rows).on_conflict_do_nothing()
    with _get_engine().begin() as connection:
        connection.execute(statement)


async def save_own_authorizations(
    account_id: str, hashes: Iterable[str], *, kind: str, recorded_at: str
) -> None:
    """Record ``hashes`` as ours; one already recorded keeps its first row."""
    await asyncio.to_thread(
        _save_own_authorizations, account_id, list(dict.fromkeys(hashes)), kind, recorded_at
    )


def _prune_own_authorizations(account_id: str, listed: list[str]) -> None:
    table = _account_own_authorizations
    statement = delete(table).where(table.c.account_id == account_id, table.c.hash.not_in(listed))
    with _get_engine().begin() as connection:
        connection.execute(statement)


async def prune_own_authorizations(account_id: str, listed: Iterable[str]) -> None:
    """Forget recorded rows whose authorization is no longer in ``listed`` (it was ended)."""
    await asyncio.to_thread(_prune_own_authorizations, account_id, list(dict.fromkeys(listed)))


def _fetch_own_authorizations(account_id: str) -> dict[str, str]:
    table = _account_own_authorizations
    with _get_engine().connect() as connection:
        rows = connection.execute(
            select(table.c.hash, table.c.kind).where(table.c.account_id == account_id)
        ).all()
    return {str(hash_): str(kind) for hash_, kind in rows}


async def fetch_own_authorizations(account_id: str) -> dict[str, str]:
    """The account's recorded own authorizations: hash to kind."""
    return await asyncio.to_thread(_fetch_own_authorizations, account_id)
