"""Users aggregate — auth credential store (issue #168)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from sqlalchemy import func, insert, select

from core.db import _get_engine, _now_iso, _users
from schemas.auth import UserRecord, UserRole

if TYPE_CHECKING:
    from sqlalchemy.engine import Row


def _to_record(row: Row[tuple[object, ...]]) -> UserRecord:
    mapping = row._mapping  # noqa: SLF001 - SQLAlchemy Row -> dict-like
    return UserRecord(
        id=str(mapping["id"]),
        username=str(mapping["username"]),
        password_hash=str(mapping["password_hash"]),
        role=cast("UserRole", str(mapping["role"])),
    )


def _create_user(record: UserRecord) -> None:
    now = _now_iso()
    statement = insert(_users).values(
        id=record.id,
        username=record.username,
        password_hash=record.password_hash,
        role=record.role,
        created_at=now,
        updated_at=now,
    )
    with _get_engine().begin() as connection:
        connection.execute(statement)


async def create_user(record: UserRecord) -> None:
    await asyncio.to_thread(_create_user, record)


def _get_user_by_username(username: str) -> UserRecord | None:
    statement = select(_users).where(_users.c.username == username)
    with _get_engine().connect() as connection:
        row = connection.execute(statement).first()
    return None if row is None else _to_record(row)


async def get_user_by_username(username: str) -> UserRecord | None:
    return await asyncio.to_thread(_get_user_by_username, username)


def _get_user_by_id(user_id: str) -> UserRecord | None:
    statement = select(_users).where(_users.c.id == user_id)
    with _get_engine().connect() as connection:
        row = connection.execute(statement).first()
    return None if row is None else _to_record(row)


async def get_user_by_id(user_id: str) -> UserRecord | None:
    return await asyncio.to_thread(_get_user_by_id, user_id)


def _count_users() -> int:
    statement = select(func.count()).select_from(_users)
    with _get_engine().connect() as connection:
        return int(connection.execute(statement).scalar_one())


async def count_users() -> int:
    return await asyncio.to_thread(_count_users)
