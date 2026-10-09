"""Account folders: list, create, rename, delete, and their memberships."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from core.db import _accounts, _get_engine, _now_iso
from core.repositories.account_folders._tables import _account_folder_members as _members
from core.repositories.account_folders._tables import _account_folders as _folders
from schemas.account_folders import AccountFolder, AccountFolders

if TYPE_CHECKING:
    from sqlalchemy import RowMapping
    from sqlalchemy.engine import Connection
    from sqlalchemy.sql import Select

# Why a write did not land: another folder already has the name, or the id is gone.
FolderMiss = Literal["name_taken", "not_found"]


def _record(row: RowMapping) -> AccountFolder:
    return AccountFolder(
        folder_id=str(row["folder_id"]),
        name=str(row["name"]),
        account_count=int(row["account_count"]),
        created_at=str(row["created_at"]),
    )


def _folder_statement() -> Select[tuple[Any, ...]]:
    count = (
        select(func.count())
        .where(_members.c.folder_id == _folders.c.folder_id)
        .scalar_subquery()
        .label("account_count")
    )
    return select(_folders.c.folder_id, _folders.c.name, _folders.c.created_at, count)


def _fetch(connection: Connection, folder_id: str) -> AccountFolder | None:
    statement = _folder_statement().where(_folders.c.folder_id == folder_id)
    row = connection.execute(statement).mappings().first()
    return None if row is None else _record(row)


def _list_folders() -> AccountFolders:
    statement = _folder_statement().order_by(_folders.c.created_at.asc(), _folders.c.name.asc())
    filed = select(func.count(func.distinct(_members.c.account_id)))
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
        total = int(connection.execute(select(func.count()).select_from(_accounts)).scalar_one())
        filed_count = int(connection.execute(filed).scalar_one())
    return AccountFolders(
        items=[_record(row) for row in rows],
        total_count=total,
        unfiled_count=total - filed_count,
    )


async def list_folders() -> AccountFolders:
    return await asyncio.to_thread(_list_folders)


def _create_folder(name: str) -> AccountFolder | FolderMiss:
    now = _now_iso()
    folder_id = uuid4().hex
    try:
        with _get_engine().begin() as connection:
            connection.execute(
                insert(_folders).values(
                    folder_id=folder_id,
                    name=name,
                    name_key=name.casefold(),
                    created_at=now,
                    updated_at=now,
                ),
            )
            return _fetch(connection, folder_id) or "not_found"
    except IntegrityError:
        return "name_taken"


async def create_folder(name: str) -> AccountFolder | FolderMiss:
    return await asyncio.to_thread(_create_folder, name)


def _rename_folder(folder_id: str, name: str) -> AccountFolder | FolderMiss:
    try:
        with _get_engine().begin() as connection:
            changed = connection.execute(
                update(_folders)
                .where(_folders.c.folder_id == folder_id)
                .values(name=name, name_key=name.casefold(), updated_at=_now_iso()),
            ).rowcount
            if not changed:
                return "not_found"
            return _fetch(connection, folder_id) or "not_found"
    except IntegrityError:
        return "name_taken"


async def rename_folder(folder_id: str, name: str) -> AccountFolder | FolderMiss:
    return await asyncio.to_thread(_rename_folder, folder_id, name)


def _delete_folder(folder_id: str) -> bool:
    # Memberships go with the folder (``ON DELETE CASCADE``); accounts stay.
    with _get_engine().begin() as connection:
        deleted = connection.execute(
            delete(_folders).where(_folders.c.folder_id == folder_id)
        ).rowcount
    return bool(deleted)


async def delete_folder(folder_id: str) -> bool:
    return await asyncio.to_thread(_delete_folder, folder_id)


def _folder_exists(connection: Connection, folder_id: str) -> bool:
    found = connection.execute(
        select(_folders.c.folder_id).where(_folders.c.folder_id == folder_id)
    ).first()
    return found is not None


def _add_members(folder_id: str, account_ids: list[str]) -> list[str] | None:
    wanted = list(dict.fromkeys(account_ids))
    with _get_engine().begin() as connection:
        if not _folder_exists(connection, folder_id):
            return None
        existing = set(
            connection.execute(
                select(_accounts.c.account_id).where(_accounts.c.account_id.in_(wanted))
            ).scalars()
        )
        present = set(
            connection.execute(
                select(_members.c.account_id).where(
                    (_members.c.folder_id == folder_id) & _members.c.account_id.in_(wanted)
                )
            ).scalars()
        )
        added = [aid for aid in wanted if aid in existing and aid not in present]
        if added:
            now = _now_iso()
            connection.execute(
                insert(_members),
                [{"folder_id": folder_id, "account_id": aid, "added_at": now} for aid in added],
            )
    return added


async def add_members(folder_id: str, account_ids: list[str]) -> list[str] | None:
    """Add accounts to a folder; the ids newly added, or ``None`` when it is gone."""
    return await asyncio.to_thread(_add_members, folder_id, account_ids)


def _remove_members(folder_id: str, account_ids: list[str]) -> list[str] | None:
    wanted = list(dict.fromkeys(account_ids))
    with _get_engine().begin() as connection:
        if not _folder_exists(connection, folder_id):
            return None
        scope = (_members.c.folder_id == folder_id) & _members.c.account_id.in_(wanted)
        present = set(connection.execute(select(_members.c.account_id).where(scope)).scalars())
        connection.execute(delete(_members).where(scope))
    return [aid for aid in wanted if aid in present]


async def remove_members(folder_id: str, account_ids: list[str]) -> list[str] | None:
    """Take accounts out of a folder; the ids actually removed, or ``None`` when it is gone."""
    return await asyncio.to_thread(_remove_members, folder_id, account_ids)


def _folder_ids_by_account() -> dict[str, list[str]]:
    statement = (
        select(_members.c.account_id, _members.c.folder_id)
        .join(_folders, _folders.c.folder_id == _members.c.folder_id)
        .order_by(_folders.c.created_at.asc(), _folders.c.name.asc())
    )
    grouped: dict[str, list[str]] = {}
    with _get_engine().connect() as connection:
        for account_id, folder_id in connection.execute(statement).all():
            grouped.setdefault(str(account_id), []).append(str(folder_id))
    return grouped


async def folder_ids_by_account() -> dict[str, list[str]]:
    """Every account's folder ids, in the folders' tab order; unfiled accounts absent."""
    return await asyncio.to_thread(_folder_ids_by_account)
