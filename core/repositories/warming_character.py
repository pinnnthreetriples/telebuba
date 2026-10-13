"""Each warming account's character — the ``warming_account_character`` table.

Written once per account (insert-or-keep: a second writer racing the first loses
quietly, so the account keeps one character), read on every chat line.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, cast

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core._schema_character import _warming_account_character
from core.db import _get_engine, _now_iso
from schemas._warming_character import AccountCharacter

if TYPE_CHECKING:
    from collections.abc import Mapping

    from schemas._warming_character import Gender


def _row_to_character(row: Mapping[str, object]) -> AccountCharacter:
    raw = json.loads(str(row["interests"]))
    return AccountCharacter(
        account_id=str(row["account_id"]),
        language=str(row["language"]),
        country=cast("str | None", row["country"]),
        age=int(cast("int", row["age"])),
        gender=cast("Gender", row["gender"]),
        city=cast("str | None", row["city"]),
        occupation=str(row["occupation"]),
        interests=[str(item) for item in raw],
    )


def _list() -> list[AccountCharacter]:
    with _get_engine().connect() as connection:
        rows = connection.execute(select(_warming_account_character)).mappings().all()
    return [_row_to_character(cast("Mapping[str, object]", row)) for row in rows]


async def list_account_characters() -> list[AccountCharacter]:
    """Every stored character (the anti-repeat check compares against all of them)."""
    return await asyncio.to_thread(_list)


def _insert(character: AccountCharacter) -> None:
    values = character.model_dump()
    values["interests"] = json.dumps(character.interests, ensure_ascii=False)
    statement = (
        sqlite_insert(_warming_account_character)
        .values(**values, created_at=_now_iso())
        .on_conflict_do_nothing(index_elements=[_warming_account_character.c.account_id])
    )
    with _get_engine().begin() as connection:
        connection.execute(statement)


async def store_account_character(character: AccountCharacter) -> None:
    """Keep the account's first character; a later one for the same account is dropped."""
    await asyncio.to_thread(_insert, character)


def _fetch(account_id: str) -> AccountCharacter | None:
    table = _warming_account_character
    with _get_engine().connect() as connection:
        row = (
            connection.execute(select(table).where(table.c.account_id == account_id))
            .mappings()
            .first()
        )
    return _row_to_character(cast("Mapping[str, object]", row)) if row is not None else None


async def fetch_account_character(account_id: str) -> AccountCharacter | None:
    """The account's stored character, ``None`` before one was made."""
    return await asyncio.to_thread(_fetch, account_id)
