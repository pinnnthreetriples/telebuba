"""The key-intent journal: one ``account_key_intents`` row per flow that makes a new auth key.

Sync helpers on a worker thread, the ``core.repositories.account_rekeys`` shape. A row is
opened before the key exists, advanced as the flow learns what a repair would need, and
closed with an outcome; a row that stays open is what a crash left behind. It goes with the
account (``core.repositories._accounts_delete``).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from sqlalchemy import insert, select, update

from core._schema_session_audits import _account_key_intents
from core.db import _get_engine
from schemas.key_intent import (
    KeyIntent,
    KeyIntentKind,
    KeyIntentOutcome,
    KeyIntentStage,
)

_TABLE = _account_key_intents


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _open_intent(values: dict[str, object]) -> int:
    with _get_engine().begin() as connection:
        result = connection.execute(insert(_TABLE).values(**values, started_at=_now()))
    key = result.inserted_primary_key
    if key is None:
        msg = "the journal row got no id"
        raise RuntimeError(msg)
    return int(key[0])


async def open_intent(  # noqa: PLR0913 - one row's columns
    *,
    account_id: str,
    kind: KeyIntentKind,
    device_type: str,
    session_name: str,
    stage: KeyIntentStage = "started",
    api_id: int | None = None,
    old_key_mark: str | None = None,
    device_json: str | None = None,
) -> int:
    """Write the intent before the key exists; its id."""
    values: dict[str, object] = {
        "account_id": account_id,
        "kind": kind,
        "device_type": device_type,
        "session_name": session_name,
        "stage": stage,
        "api_id": api_id,
        "old_key_mark": old_key_mark,
        "device_json": device_json,
    }
    return await asyncio.to_thread(_open_intent, values)


def _advance_intent(intent_id: int, values: dict[str, object]) -> None:
    with _get_engine().begin() as connection:
        connection.execute(update(_TABLE).where(_TABLE.c.id == intent_id).values(**values))


async def advance_intent(
    intent_id: int,
    *,
    stage: KeyIntentStage | None = None,
    new_hash: str | None = None,
    old_hash: str | None = None,
) -> None:
    """Record what the flow learned; a field left ``None`` keeps what the row has."""
    values: dict[str, object] = {
        key: value
        for key, value in (("stage", stage), ("new_hash", new_hash), ("old_hash", old_hash))
        if value is not None
    }
    if values:
        await asyncio.to_thread(_advance_intent, intent_id, values)


def _close_intent(intent_id: int, outcome: str) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            update(_TABLE)
            .where(_TABLE.c.id == intent_id, _TABLE.c.closed_at.is_(None))
            .values(closed_at=_now(), outcome=outcome)
        )


async def close_intent(intent_id: int, outcome: KeyIntentOutcome) -> None:
    """Close the intent (a closed one keeps its first outcome)."""
    await asyncio.to_thread(_close_intent, intent_id, outcome)


def _fetch_intent(intent_id: int) -> KeyIntent | None:
    with _get_engine().connect() as connection:
        row = connection.execute(select(_TABLE).where(_TABLE.c.id == intent_id)).first()
    return None if row is None else KeyIntent.model_validate(dict(row._mapping))  # noqa: SLF001


async def fetch_intent(intent_id: int) -> KeyIntent | None:
    return await asyncio.to_thread(_fetch_intent, intent_id)


def _list_open_intents(account_id: str | None) -> list[KeyIntent]:
    query = select(_TABLE).where(_TABLE.c.closed_at.is_(None)).order_by(_TABLE.c.id)
    if account_id is not None:
        query = query.where(_TABLE.c.account_id == account_id)
    with _get_engine().connect() as connection:
        rows = connection.execute(query).all()
    return [KeyIntent.model_validate(dict(row._mapping)) for row in rows]  # noqa: SLF001


async def list_open_intents(account_id: str | None = None) -> list[KeyIntent]:
    """Intents no flow has closed, oldest first (one account's, or every account's)."""
    return await asyncio.to_thread(_list_open_intents, account_id)
