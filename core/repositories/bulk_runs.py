"""Persistent bulk-message runs — ``bulk_runs``, ``bulk_run_items``, ``bulk_run_accounts``.

The runner keeps its working copy in memory and writes every change through here,
so a restart resumes from the rows. Writes are small and per row on purpose: a
crash between two sends loses nothing but the send in flight, which resume marks
unconfirmed.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError

from core._schema_bulk_runs import _bulk_run_accounts, _bulk_run_items, _bulk_runs
from core.db import _get_engine, _now_iso
from schemas.bulk_runs import BulkRunAccount, BulkRunItem, BulkRunRecord

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy import Select

_LIVE = "running"


class BulkRunActiveError(RuntimeError):
    """Another run is already live; the unique live-run index refused this one."""


_ACCOUNT_KEY = frozenset({"run_id", "account_id"})


def _create_bulk_run(
    record: BulkRunRecord,
    items: Sequence[BulkRunItem],
    accounts: Sequence[BulkRunAccount],
) -> None:
    now = _now_iso()
    with _get_engine().begin() as connection:
        try:
            connection.execute(insert(_bulk_runs).values(**record.model_dump(), updated_at=now))
        except IntegrityError as exc:
            raise BulkRunActiveError(record.id) from exc
        if items:
            connection.execute(
                insert(_bulk_run_items),
                [item.model_dump(exclude={"id"}) for item in items],
            )
        if accounts:
            connection.execute(
                insert(_bulk_run_accounts),
                [account.model_dump() for account in accounts],
            )


async def create_bulk_run(
    record: BulkRunRecord,
    items: Sequence[BulkRunItem],
    accounts: Sequence[BulkRunAccount],
) -> None:
    """Store a new run with its planned sends and accounts in one transaction."""
    await asyncio.to_thread(_create_bulk_run, record, items, accounts)


def _select_runs(statement: Select[tuple[object, ...]]) -> list[BulkRunRecord]:
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [BulkRunRecord.model_validate(dict(row)) for row in rows]


async def get_bulk_run(run_id: str) -> BulkRunRecord | None:
    runs = await asyncio.to_thread(
        _select_runs, select(_bulk_runs).where(_bulk_runs.c.id == run_id)
    )
    return runs[0] if runs else None


async def list_live_bulk_runs() -> list[BulkRunRecord]:
    """Runs still sending or waiting — what a restart resumes."""
    return await asyncio.to_thread(
        _select_runs,
        select(_bulk_runs).where(_bulk_runs.c.status == _LIVE).order_by(_bulk_runs.c.created_at),
    )


async def latest_bulk_run(owner_user_id: str, *, live_only: bool) -> BulkRunRecord | None:
    statement = select(_bulk_runs).where(_bulk_runs.c.owner_user_id == owner_user_id)
    if live_only:
        statement = statement.where(_bulk_runs.c.status == _LIVE)
    runs = await asyncio.to_thread(
        _select_runs, statement.order_by(_bulk_runs.c.created_at.desc()).limit(1)
    )
    return runs[0] if runs else None


def _list_items(run_id: str) -> list[BulkRunItem]:
    statement = (
        select(_bulk_run_items)
        .where(_bulk_run_items.c.run_id == run_id)
        .order_by(_bulk_run_items.c.ordinal, _bulk_run_items.c.id)
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [BulkRunItem.model_validate(dict(row)) for row in rows]


async def list_bulk_run_items(run_id: str) -> list[BulkRunItem]:
    return await asyncio.to_thread(_list_items, run_id)


def _list_accounts(run_id: str) -> list[BulkRunAccount]:
    statement = select(_bulk_run_accounts).where(_bulk_run_accounts.c.run_id == run_id)
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [BulkRunAccount.model_validate(dict(row)) for row in rows]


async def list_bulk_run_accounts(run_id: str) -> list[BulkRunAccount]:
    return await asyncio.to_thread(_list_accounts, run_id)


def _save_item(item: BulkRunItem) -> int:
    values = item.model_dump(exclude={"id"})
    with _get_engine().begin() as connection:
        if item.id is None:
            result = connection.execute(insert(_bulk_run_items).values(**values))
            return int(result.lastrowid)
        connection.execute(
            update(_bulk_run_items).where(_bulk_run_items.c.id == item.id).values(**values)
        )
        return item.id


async def save_bulk_run_item(item: BulkRunItem) -> int:
    """Insert (no ``id``) or overwrite one item; returns its id."""
    return await asyncio.to_thread(_save_item, item)


def _save_account(account: BulkRunAccount) -> None:
    table = _bulk_run_accounts
    values = account.model_dump()
    statement = sqlite_insert(table).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=[table.c.run_id, table.c.account_id],
        set_={
            name: statement.excluded[name]
            for name in values
            if name not in {"run_id", "account_id"}
        },
    )
    with _get_engine().begin() as connection:
        connection.execute(statement)


async def save_bulk_run_account(account: BulkRunAccount) -> None:
    await asyncio.to_thread(_save_account, account)


def _update_run(run_id: str, values: dict[str, object]) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            update(_bulk_runs)
            .where(_bulk_runs.c.id == run_id)
            .values(**values, updated_at=_now_iso())
        )


async def update_bulk_run(run_id: str, **values: object) -> None:
    await asyncio.to_thread(_update_run, run_id, values)


def _delete_item(item_id: int) -> None:
    with _get_engine().begin() as connection:
        connection.execute(delete(_bulk_run_items).where(_bulk_run_items.c.id == item_id))


async def delete_bulk_run_item(item_id: int) -> None:
    await asyncio.to_thread(_delete_item, item_id)


def _unconfirm_sending(run_id: str) -> int:
    with _get_engine().begin() as connection:
        result = connection.execute(
            update(_bulk_run_items)
            .where((_bulk_run_items.c.run_id == run_id) & (_bulk_run_items.c.status == "sending"))
            .values(status="unconfirmed", error_code="delivery_unconfirmed")
        )
    return result.rowcount


async def unconfirm_sending_items(run_id: str) -> int:
    """A send a restart interrupted may have gone out: never retry it, mark it unconfirmed."""
    return await asyncio.to_thread(_unconfirm_sending, run_id)


def _prune(keep: int) -> int:
    finished = (
        select(_bulk_runs.c.id)
        .where(_bulk_runs.c.status != _LIVE)
        .order_by(_bulk_runs.c.created_at.desc())
        .offset(keep)
    )
    with _get_engine().begin() as connection:
        ids = [str(row[0]) for row in connection.execute(finished).all()]
        if ids:
            connection.execute(delete(_bulk_run_items).where(_bulk_run_items.c.run_id.in_(ids)))
            connection.execute(
                delete(_bulk_run_accounts).where(_bulk_run_accounts.c.run_id.in_(ids))
            )
            connection.execute(delete(_bulk_runs).where(_bulk_runs.c.id.in_(ids)))
    return len(ids)


async def prune_bulk_runs(keep: int) -> int:
    """Delete finished runs beyond the newest ``keep``; returns how many went."""
    return await asyncio.to_thread(_prune, keep)


def _max_finished_seq(run_id: str) -> int:
    statement = select(func.max(_bulk_run_items.c.finished_seq)).where(
        _bulk_run_items.c.run_id == run_id
    )
    with _get_engine().connect() as connection:
        return int(connection.execute(statement).scalar() or 0)


async def max_finished_seq(run_id: str) -> int:
    return await asyncio.to_thread(_max_finished_seq, run_id)
