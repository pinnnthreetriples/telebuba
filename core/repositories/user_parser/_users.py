"""The people a run kept: pages for the modal, chunks for the export, earlier finds."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, cast

from sqlalchemy import func, select

from core.db import _get_engine
from core.repositories.user_parser._tables import _user_parser_users as _users
from schemas.user_parser_run import UserParserUser, UserParserUserPage

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sqlalchemy import RowMapping
    from sqlalchemy.sql import ColumnElement

    from schemas.telegram_actions_user_parser import LastSeenBucket

_FLAGS = (
    "is_bot",
    "is_deleted",
    "is_scam",
    "is_fake",
    "is_premium",
    "has_photo",
    "has_stories",
)
# SQLite ``id IN (...)`` binds one parameter per id; stay well under its 32 766 cap.
_IN_CHUNK = 900


def _user(row: RowMapping) -> UserParserUser:
    return UserParserUser(
        user_id=int(row["user_id"]),
        username=row["username"],
        first_name=str(row["first_name"]),
        last_name=str(row["last_name"]),
        last_seen=cast("LastSeenBucket", row["last_seen"]),
        message_count=int(row["message_count"]),
        first_at=row["first_at"],
        last_at=row["last_at"],
        sources=list(json.loads(row["sources_json"])),
        **{flag: bool(row[flag]) for flag in _FLAGS},
    )


def _escape_like(needle: str) -> str:
    return needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _scope(run_id: str, search: str) -> ColumnElement[bool]:
    clause = _users.c.run_id == run_id
    needle = search.strip().casefold()
    if needle:
        clause = clause & _users.c.search_key.like(f"%{_escape_like(needle)}%", escape="\\")
    return clause


def _page_users(run_id: str, search: str, offset: int, limit: int) -> UserParserUserPage:
    scope = _scope(run_id, search)
    statement = (
        select(_users)
        .where(scope)
        .order_by(_users.c.message_count.desc(), _users.c.user_id.asc())
        .offset(offset)
        .limit(limit)
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
        total = int(connection.execute(select(func.count()).where(scope)).scalar_one())
    return UserParserUserPage(items=[_user(row) for row in rows], total=total)


async def page_users(run_id: str, *, search: str, offset: int, limit: int) -> UserParserUserPage:
    """One page of a base's people, most active first; ``search`` matches name, nick, id."""
    return await asyncio.to_thread(_page_users, run_id, search, offset, limit)


def _users_after(run_id: str, after_user_id: int | None, limit: int) -> list[UserParserUser]:
    clause = _users.c.run_id == run_id
    if after_user_id is not None:
        clause = clause & (_users.c.user_id > after_user_id)
    statement = select(_users).where(clause).order_by(_users.c.user_id.asc()).limit(limit)
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [_user(row) for row in rows]


async def users_after(run_id: str, after_user_id: int | None, limit: int) -> list[UserParserUser]:
    """The next chunk of a base in id order — the export walks it with this cursor."""
    return await asyncio.to_thread(_users_after, run_id, after_user_id, limit)


def _collected_user_ids(user_ids: list[int], exclude_run_id: str) -> set[int]:
    found: set[int] = set()
    with _get_engine().connect() as connection:
        for start in range(0, len(user_ids), _IN_CHUNK):
            chunk = user_ids[start : start + _IN_CHUNK]
            statement = select(_users.c.user_id).where(
                _users.c.user_id.in_(chunk) & (_users.c.run_id != exclude_run_id)
            )
            found.update(int(value) for value in connection.execute(statement).scalars())
    return found


async def collected_user_ids(user_ids: Iterable[int], *, exclude_run_id: str) -> set[int]:
    """Of these people, the ones some other base already holds."""
    return await asyncio.to_thread(_collected_user_ids, list(user_ids), exclude_run_id)
