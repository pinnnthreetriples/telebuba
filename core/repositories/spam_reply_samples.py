"""Redacted reply samples of @SpamBot — real texts for building parser fixtures.

One row per distinct normalised text (``text_hash``), with a counter, so a probe that
answers the same words every time costs one row. The table is bounded: past
``max_rows`` the least recently seen rows go, and :func:`prune_reply_samples` drops the old
ones by age. The text is redacted by the caller (``services._spam_redact.redact_reply``) before
it reaches this module.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core._schema_spam_intel import _spam_reply_samples
from core.db import _get_engine
from schemas.spam_intel import ReplySample

_FLAGS = ("has_date", "multi_message", "has_markup")


def _upsert_sample(sample: ReplySample, max_rows: int) -> None:
    table = _spam_reply_samples
    values = sample.model_dump()
    for flag in _FLAGS:
        values[flag] = int(getattr(sample, flag))
    statement = sqlite_insert(table).values(**values)
    with _get_engine().begin() as connection:
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=[table.c.text_hash],
                set_={
                    "last_seen_at": sample.last_seen_at,
                    "seen_count": table.c.seen_count + 1,
                    "legacy_status": sample.legacy_status,
                    "strict_status": sample.strict_status,
                    # A flag once seen stays: the same text may come single and in a pair.
                    "multi_message": func.max(table.c.multi_message, int(sample.multi_message)),
                    "has_markup": func.max(table.c.has_markup, int(sample.has_markup)),
                },
            )
        )
        total = connection.execute(select(func.count()).select_from(table)).scalar() or 0
        if total > max_rows:
            stale = (
                select(table.c.text_hash)
                .order_by(table.c.last_seen_at, table.c.text_hash)
                .limit(total - max_rows)
            )
            connection.execute(delete(table).where(table.c.text_hash.in_(stale)))


def _prune_older_than(cutoff: str) -> None:
    table = _spam_reply_samples
    with _get_engine().begin() as connection:
        connection.execute(delete(table).where(table.c.last_seen_at < cutoff))


async def prune_reply_samples(older_than: str) -> None:
    """Drop every sample last seen before ``older_than`` (an ISO timestamp)."""
    await asyncio.to_thread(_prune_older_than, older_than)


async def upsert_reply_sample(sample: ReplySample, max_rows: int) -> None:
    """Store a sample, or count one more sighting of the same text; keep the table bounded."""
    await asyncio.to_thread(_upsert_sample, sample, max_rows)


def _list_samples() -> list[ReplySample]:
    table = _spam_reply_samples
    with _get_engine().connect() as connection:
        rows = connection.execute(select(table).order_by(table.c.last_seen_at.desc())).all()
    out: list[ReplySample] = []
    for row in rows:
        data = dict(row._mapping)  # noqa: SLF001
        for flag in _FLAGS:
            data[flag] = bool(data[flag])
        out.append(ReplySample.model_validate(data))
    return out


async def list_reply_samples() -> list[ReplySample]:
    """Every stored sample, most recently seen first."""
    return await asyncio.to_thread(_list_samples)
