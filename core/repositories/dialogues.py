"""Dialogue-pairing repository (#40).

Owns the ``dialogue_pairs`` table (undirected acquaintance pairs between warming
accounts). New table registers with the shared ``core.db`` metadata; the public
functions are re-exported by ``core.db``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from sqlalchemy import (
    Column,
    Index,
    Integer,
    String,
    Table,
    delete,
    func,
    insert,
    select,
    update,
)

from core.db import _get_engine, _metadata, _now_iso
from schemas.dialogues import DialogueMessage, DialoguePair

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


dialogue_messages = Table(
    "dialogue_messages",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("pair_key", String, nullable=False),
    Column("from_account", String, nullable=False),
    Column("to_account", String, nullable=False),
    Column("text", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("replied", Integer, nullable=False),
    # Hot paths: latest_unreplied_for filters by (to_account, replied) then orders by id;
    # count_pair_messages_since filters by (pair_key, created_at). Without these indexes
    # both degrade to full table scans once the table accumulates history.
    Index("ix_dialogue_messages_inbox", "to_account", "replied", "id"),
    Index("ix_dialogue_messages_pair_time", "pair_key", "created_at"),
)


def pair_key(account_a: str, account_b: str) -> str:
    """Canonical key for the unordered pair (order-independent)."""
    return "|".join(sorted((account_a, account_b)))


def _row_to_message(mapping: Mapping[str, object]) -> DialogueMessage:
    return DialogueMessage(
        id=int(cast("int", mapping["id"])),
        pair_key=str(mapping["pair_key"]),
        from_account=str(mapping["from_account"]),
        to_account=str(mapping["to_account"]),
        text=str(mapping["text"]),
        created_at=str(mapping["created_at"]),
        replied=bool(mapping["replied"]),
    )


def _record_dialogue_message(
    from_account: str,
    to_account: str,
    text: str,
    *,
    replied: bool,
) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            insert(dialogue_messages).values(
                pair_key=pair_key(from_account, to_account),
                from_account=from_account,
                to_account=to_account,
                text=text,
                created_at=_now_iso(),
                replied=int(replied),
            ),
        )


async def record_dialogue_message(
    from_account: str,
    to_account: str,
    text: str,
    *,
    replied: bool = False,
) -> None:
    """Persist one message between two accounts."""
    await asyncio.to_thread(
        _record_dialogue_message,
        from_account,
        to_account,
        text,
        replied=replied,
    )


def _latest_unreplied_for(account_id: str) -> DialogueMessage | None:
    statement = (
        select(dialogue_messages)
        .where(
            (dialogue_messages.c.to_account == account_id) & (dialogue_messages.c.replied == 0),
        )
        .order_by(dialogue_messages.c.id.desc())
        .limit(1)
    )
    with _get_engine().connect() as connection:
        row = connection.execute(statement).mappings().first()
    return None if row is None else _row_to_message(cast("Mapping[str, object]", row))


async def latest_unreplied_for(account_id: str) -> DialogueMessage | None:
    """The most recent message awaiting a reply from ``account_id``, if any."""
    return await asyncio.to_thread(_latest_unreplied_for, account_id)


def _mark_message_replied(message_id: int) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            update(dialogue_messages).where(dialogue_messages.c.id == message_id).values(replied=1),
        )


async def mark_message_replied(message_id: int) -> None:
    """Mark a message as replied so it is not answered again."""
    await asyncio.to_thread(_mark_message_replied, message_id)


def _try_claim_message_reply(message_id: int) -> bool:
    statement = (
        update(dialogue_messages)
        .where(
            (dialogue_messages.c.id == message_id) & (dialogue_messages.c.replied == 0),
        )
        .values(replied=1)
    )
    with _get_engine().begin() as connection:
        result = connection.execute(statement)
        return result.rowcount > 0


async def try_claim_message_reply(message_id: int) -> bool:
    """Atomically mark a message as replied iff no one else has — returns True on success.

    Use this before sending a DM so two parallel cycles cannot both answer the
    same incoming message. The non-atomic ``latest_unreplied_for`` + ``mark``
    split could race; this collapses the claim into a single
    ``UPDATE ... WHERE replied=0`` whose ``rowcount`` is the source of truth.
    """
    return await asyncio.to_thread(_try_claim_message_reply, message_id)


def _mark_message_unreplied(message_id: int) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            update(dialogue_messages).where(dialogue_messages.c.id == message_id).values(replied=0),
        )


async def mark_message_unreplied(message_id: int) -> None:
    """Release a previously-claimed reply so the next cycle can retry it (F6).

    Paired with :func:`try_claim_message_reply`: claim before send to dedupe
    parallel cycles, then release on flood / transient send failure so the
    incoming message stays in the inbox instead of disappearing.
    """
    await asyncio.to_thread(_mark_message_unreplied, message_id)


def _count_pair_messages_since(key: str, since_iso: str) -> int:
    statement = (
        select(func.count())
        .select_from(dialogue_messages)
        .where(
            (dialogue_messages.c.pair_key == key) & (dialogue_messages.c.created_at >= since_iso),
        )
    )
    with _get_engine().connect() as connection:
        return int(connection.execute(statement).scalar_one())


async def count_pair_messages_since(key: str, since_iso: str) -> int:
    """Count messages exchanged in a pair since ``since_iso`` (for fade-out)."""
    return await asyncio.to_thread(_count_pair_messages_since, key, since_iso)


def _list_recent_dialogue_messages(limit: int) -> list[DialogueMessage]:
    statement = select(dialogue_messages).order_by(dialogue_messages.c.id.desc()).limit(limit)
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [_row_to_message(cast("Mapping[str, object]", row)) for row in rows]


async def list_recent_dialogue_messages(limit: int = 30) -> list[DialogueMessage]:
    """Return the most recent dialogue messages, newest first (for the UI feed)."""
    return await asyncio.to_thread(_list_recent_dialogue_messages, limit)


def _purge_dialogue_messages_older_than(cutoff_iso: str) -> int:
    # Only purge already-replied messages — unreplied ones may still owe an
    # answer even if old, and dropping them would break ongoing conversations.
    statement = delete(dialogue_messages).where(
        (dialogue_messages.c.created_at < cutoff_iso) & (dialogue_messages.c.replied == 1),
    )
    with _get_engine().begin() as connection:
        return connection.execute(statement).rowcount


async def purge_dialogue_messages_older_than(cutoff_iso: str) -> int:
    """Delete replied dialogue messages older than the cutoff. Returns rows removed."""
    return await asyncio.to_thread(_purge_dialogue_messages_older_than, cutoff_iso)
