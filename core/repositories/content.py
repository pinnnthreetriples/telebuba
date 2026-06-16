"""Sent-message hash repository (#42) — project-wide content de-duplication.

Stores a normalised-text hash per outbound message so the warming/dialogue
engines can refuse to send the same text twice within a window (identical
content across accounts is a strong spam signal). New table registers with the
shared ``core.db`` metadata; the public functions are re-exported by ``core.db``.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import Column, String, Table, insert, update

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
    now = _now_iso()
    # Single transaction: see whether the same text was sent inside the dedup
    # window; if not, insert/refresh the row immediately. Two concurrent senders
    # cannot both win because the second waits on the write lock (WAL +
    # busy_timeout) and then sees the freshly-written row.
    with _get_engine().begin() as connection:
        existing = connection.execute(
            sent_message_hashes.select().where(
                (sent_message_hashes.c.text_hash == text_hash)
                & (sent_message_hashes.c.created_at >= since_iso),
            ),
        ).first()
        if existing is not None:
            return False
        updated = connection.execute(
            update(sent_message_hashes)
            .where(sent_message_hashes.c.text_hash == text_hash)
            .values(created_at=now),
        )
        if updated.rowcount == 0:
            connection.execute(
                insert(sent_message_hashes).values(text_hash=text_hash, created_at=now),
            )
        return True


async def try_reserve_sent_hash(text_hash: str, since_iso: str) -> bool:
    """Atomically reserve a content hash before sending — True if claim wins.

    Replaces the non-atomic ``was_hash_sent_since`` then ``record_sent_hash``
    split: two concurrent senders of the same text can no longer both observe
    "not yet sent" before either records the hash. A False return means another
    sender already reserved this text within the dedup window.
    """
    return await asyncio.to_thread(_try_reserve_sent_hash, text_hash, since_iso)
