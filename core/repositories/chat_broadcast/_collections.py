"""Saved chat lists: list, create, replace, delete. Names are unique."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from core.db import _get_engine, _now_iso
from core.repositories.chat_broadcast._tables import _chat_broadcast_collections as _TABLE
from schemas.chat_broadcast_collections import ChatCollection

if TYPE_CHECKING:
    from sqlalchemy import RowMapping

# Why a write did not land: another list already has the name, or the id is gone.
CollectionMiss = Literal["name_taken", "not_found"]


def _record(row: RowMapping) -> ChatCollection:
    return ChatCollection(
        collection_id=str(row["collection_id"]),
        name=str(row["name"]),
        targets=list(json.loads(row["targets_json"] or "[]")),
        updated_at=str(row["updated_at"]),
    )


def _list_collections() -> list[ChatCollection]:
    statement = select(_TABLE).order_by(_TABLE.c.name.asc())
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [_record(row) for row in rows]


async def list_collections() -> list[ChatCollection]:
    return await asyncio.to_thread(_list_collections)


def _fetch(collection_id: str) -> ChatCollection | None:
    statement = select(_TABLE).where(_TABLE.c.collection_id == collection_id)
    with _get_engine().connect() as connection:
        row = connection.execute(statement).mappings().first()
    return None if row is None else _record(row)


def _create_collection(name: str, targets: list[str]) -> ChatCollection | CollectionMiss:
    now = _now_iso()
    collection_id = uuid4().hex
    try:
        with _get_engine().begin() as connection:
            connection.execute(
                insert(_TABLE).values(
                    collection_id=collection_id,
                    name=name,
                    targets_json=json.dumps(targets, ensure_ascii=False),
                    created_at=now,
                    updated_at=now,
                ),
            )
    except IntegrityError:
        return "name_taken"
    return _fetch(collection_id) or "not_found"


async def create_collection(name: str, targets: list[str]) -> ChatCollection | CollectionMiss:
    return await asyncio.to_thread(_create_collection, name, targets)


def _replace_collection(
    collection_id: str, name: str, targets: list[str]
) -> ChatCollection | CollectionMiss:
    try:
        with _get_engine().begin() as connection:
            changed = connection.execute(
                update(_TABLE)
                .where(_TABLE.c.collection_id == collection_id)
                .values(
                    name=name,
                    targets_json=json.dumps(targets, ensure_ascii=False),
                    updated_at=_now_iso(),
                ),
            ).rowcount
    except IntegrityError:
        return "name_taken"
    if not changed:
        return "not_found"
    return _fetch(collection_id) or "not_found"


async def replace_collection(
    collection_id: str, name: str, targets: list[str]
) -> ChatCollection | CollectionMiss:
    return await asyncio.to_thread(_replace_collection, collection_id, name, targets)


def _delete_collection(collection_id: str) -> bool:
    with _get_engine().begin() as connection:
        deleted = connection.execute(
            delete(_TABLE).where(_TABLE.c.collection_id == collection_id)
        ).rowcount
    return bool(deleted)


async def delete_collection(collection_id: str) -> bool:
    return await asyncio.to_thread(_delete_collection, collection_id)
