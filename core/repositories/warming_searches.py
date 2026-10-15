"""The history of warming channel searches — what spaces the next search out.

One row per search: a warming account's own (``self``), the automatic refill's
(``refill``) or the operator's (``manual``). Rows older than the retention are pruned
on every write, so the table stays small without a sweep of its own.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

from sqlalchemy import delete, insert, select

from core._schema_channel_search import _warming_channel_searches
from core.db import _get_engine, _optional_str
from schemas._warming_pool import ChannelSearchRecord

if TYPE_CHECKING:
    from collections.abc import Mapping

# Longer than any window a reader asks about (a day, a week, the refill spacing).
_RETENTION = timedelta(days=30)


def _record(row: ChannelSearchRecord) -> None:
    table = _warming_channel_searches
    cutoff = (datetime.now(UTC) - _RETENTION).isoformat()
    with _get_engine().begin() as connection:
        connection.execute(delete(table).where(table.c.created_at < cutoff))
        connection.execute(insert(table).values(row.model_dump()))


async def record_channel_search(row: ChannelSearchRecord) -> None:
    """Remember one search (and forget the ones past the retention)."""
    await asyncio.to_thread(_record, row)


def _to_record(mapping: Mapping[str, object]) -> ChannelSearchRecord:
    return ChannelSearchRecord.model_validate(
        {**mapping, "account_id": _optional_str(mapping.get("account_id"))}
    )


def _since(since: str, account_id: str | None, language: str | None) -> list[ChannelSearchRecord]:
    table = _warming_channel_searches
    statement = select(
        table.c.origin,
        table.c.account_id,
        table.c.language,
        table.c.status,
        table.c.found,
        table.c.added,
        table.c.created_at,
    ).where(table.c.created_at >= since)
    if account_id is not None:
        statement = statement.where(table.c.account_id == account_id)
    if language is not None:
        statement = statement.where(table.c.language == language)
    with _get_engine().connect() as connection:
        rows = connection.execute(statement.order_by(table.c.id.asc())).mappings().all()
    return [_to_record(cast("Mapping[str, object]", row)) for row in rows]


async def list_channel_searches_since(
    since: datetime,
    *,
    account_id: str | None = None,
    language: str | None = None,
) -> list[ChannelSearchRecord]:
    """The searches since ``since``, oldest first, narrowed to an account or a language."""
    return await asyncio.to_thread(_since, since.isoformat(), account_id, language)
