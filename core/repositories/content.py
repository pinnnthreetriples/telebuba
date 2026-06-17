"""Sent-message hash repository (#42) — project-wide content de-duplication.

Stores a normalised-text hash per outbound message so the warming/dialogue
engines can refuse to send the same text twice within a window (identical
content across accounts is a strong spam signal). New table registers with the
shared ``core.db`` metadata; the public functions are re-exported by ``core.db``.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import Column, String, Table, delete, insert, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core.db import _get_engine, _metadata, _now_iso

sent_message_hashes = Table(
    "sent_message_hashes",
    _metadata,
    Column("text_hash", String, primary_key=True),
    Column("created_at", String, nullable=False),
)


def _was_hash_sent_since(text_hash: str, since_iso: str) -> bool:
    statement = sent_message_hashes.select().where(
        (sent_message_hashes.c.text_hash == text_hash)
        & (sent_message_hashes.c.created_at >= since_iso),
    )
    with _get_engine().connect() as connection:
        return connection.execute(statement).first() is not None


async def was_hash_sent_since(text_hash: str, since_iso: str) -> bool:
    """True if ``text_hash`` was recorded at or after ``since_iso``."""
    return await asyncio.to_thread(_was_hash_sent_since, text_hash, since_iso)


def _record_sent_hash(text_hash: str) -> None:
    now = _now_iso()
    with _get_engine().begin() as connection:
        updated = connection.execute(
            update(sent_message_hashes)
            .where(sent_message_hashes.c.text_hash == text_hash)
            .values(created_at=now),
        )
        if updated.rowcount == 0:
            connection.execute(
                insert(sent_message_hashes).values(text_hash=text_hash, created_at=now),
            )


async def record_sent_hash(text_hash: str) -> None:
    """Record (or refresh) the timestamp for an outbound-message hash."""
    await asyncio.to_thread(_record_sent_hash, text_hash)


def _try_reserve_sent_hash(text_hash: str, since_iso: str) -> bool:
    # F7: previous implementation did SELECT → UPDATE → INSERT in a deferred
    # transaction, which under WAL snapshot isolation let two senders both
    # observe "no row" and then race on PK insert (one wins, the other raises
    # IntegrityError into the caller). Replaced with a single
    # ``INSERT ... ON CONFLICT DO UPDATE`` that takes the write lock atomically
    # and reports via RETURNING whether the pre-existing row was inside the
    # dedup window. SQLite ≥ 3.35 ships RETURNING; the project pins a recent
    # Python (3.13) whose bundled sqlite easily meets that.
    now = _now_iso()
    stmt = (
        sqlite_insert(sent_message_hashes)
        .values(text_hash=text_hash, created_at=now)
        .on_conflict_do_update(
            index_elements=[sent_message_hashes.c.text_hash],
            set_={"created_at": now},
            where=sent_message_hashes.c.created_at < since_iso,
        )
        .returning(sent_message_hashes.c.created_at)
    )
    with _get_engine().begin() as connection:
        row = connection.execute(stmt).first()
    # ``row`` is the created_at AFTER the upsert. If the upsert refreshed
    # ``created_at`` to ``now`` (insert path OR conflict outside dedup window),
    # the caller wins the reservation. If the WHERE clause skipped the update
    # (conflict inside dedup window), RETURNING yields no row.
    return row is not None and row[0] == now


async def try_reserve_sent_hash(text_hash: str, since_iso: str) -> bool:
    """Atomically reserve a content hash before sending — True if claim wins.

    Replaces the non-atomic ``was_hash_sent_since`` then ``record_sent_hash``
    split: two concurrent senders of the same text can no longer both observe
    "not yet sent" before either records the hash. A False return means another
    sender already reserved this text within the dedup window.
    """
    return await asyncio.to_thread(_try_reserve_sent_hash, text_hash, since_iso)


def _release_sent_hash(text_hash: str) -> int:
    statement = delete(sent_message_hashes).where(sent_message_hashes.c.text_hash == text_hash)
    with _get_engine().begin() as connection:
        return connection.execute(statement).rowcount


async def release_sent_hash(text_hash: str) -> int:
    """Drop a previously-reserved hash so the same text can be retried (P2.6).

    Used by the chat path when a send fails after a successful reservation:
    without releasing, the text stays locked for the full dedup window and the
    next cycle is forced to filter it as a duplicate — so a transient flood
    on a Gemini-generated reply would permanently shadow the same text. The
    delete is unconditional; the caller is the only writer who could have
    inserted this row in the inter-send window.
    """
    return await asyncio.to_thread(_release_sent_hash, text_hash)


def _purge_sent_hashes_older_than(cutoff_iso: str) -> int:
    statement = delete(sent_message_hashes).where(sent_message_hashes.c.created_at < cutoff_iso)
    with _get_engine().begin() as connection:
        return connection.execute(statement).rowcount


async def purge_sent_hashes_older_than(cutoff_iso: str) -> int:
    """Delete sent-hash rows older than the cutoff. Returns rows removed.

    Anything past the dedup window is dead weight: it cannot block any current
    send, so retaining it just bloats the table.
    """
    return await asyncio.to_thread(_purge_sent_hashes_older_than, cutoff_iso)
