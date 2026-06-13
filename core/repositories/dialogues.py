"""Dialogue-pairing repository (#40).

Owns the ``dialogue_pairs`` table (undirected acquaintance pairs between warming
accounts). New table registers with the shared ``core.db`` metadata; the public
functions are re-exported by ``core.db``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from sqlalchemy import Column, String, Table, delete, insert, select

from core.db import _get_engine, _metadata, _now_iso
from schemas.dialogues import DialoguePair

if TYPE_CHECKING:
    from collections.abc import Mapping

dialogue_pairs = Table(
    "dialogue_pairs",
    _metadata,
    Column("account_a", String, primary_key=True),
    Column("account_b", String, primary_key=True),
    Column("assigned_at", String, nullable=False),
)


def _row_to_pair(mapping: Mapping[str, object]) -> DialoguePair:
    return DialoguePair(
        account_a=str(mapping["account_a"]),
        account_b=str(mapping["account_b"]),
        assigned_at=str(mapping["assigned_at"]),
    )


def _list_dialogue_pairs() -> list[DialoguePair]:
    with _get_engine().connect() as connection:
        rows = connection.execute(select(dialogue_pairs)).mappings().all()
    return [_row_to_pair(cast("Mapping[str, object]", row)) for row in rows]


async def list_dialogue_pairs() -> list[DialoguePair]:
    """Return every stored acquaintance pair."""
    return await asyncio.to_thread(_list_dialogue_pairs)


def _replace_dialogue_pairs(pairs: list[tuple[str, str]]) -> None:
    now = _now_iso()
    with _get_engine().begin() as connection:
        connection.execute(delete(dialogue_pairs))
        for account_a, account_b in pairs:
            connection.execute(
                insert(dialogue_pairs).values(
                    account_a=account_a,
                    account_b=account_b,
                    assigned_at=now,
                ),
            )


async def replace_dialogue_pairs(pairs: list[tuple[str, str]]) -> None:
    """Atomically replace all pairs with ``pairs`` (each canonical: a < b)."""
    await asyncio.to_thread(_replace_dialogue_pairs, pairs)
