"""Spam-status cache repository (split out of core.db for #38).

Owns reads/writes of the ``account_spam_status`` table. Shared plumbing (engine,
table object, row helpers) is imported from ``core.db``; the public async
functions are re-exported by ``core.db`` so callers are unaffected.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from sqlalchemy import insert, select, update

from core.db import _account_spam_status, _get_engine, _optional_str
from schemas.spam_status import SpamStatusKind, SpamStatusVerdict

if TYPE_CHECKING:
    from collections.abc import Mapping


def _row_to_spam_status(mapping: Mapping[str, object]) -> SpamStatusVerdict:
    return SpamStatusVerdict(
        account_id=str(mapping["account_id"]),
        status=cast("SpamStatusKind", str(mapping["status"])),
        detail=_optional_str(mapping.get("detail")),
        checked_at=str(mapping["checked_at"]),
    )


def _get_spam_status(account_id: str) -> SpamStatusVerdict | None:
    statement = select(_account_spam_status).where(
        _account_spam_status.c.account_id == account_id,
    )
    with _get_engine().connect() as connection:
        row = connection.execute(statement).mappings().first()
    if row is None:
        return None
    return _row_to_spam_status(cast("Mapping[str, object]", row))


async def get_spam_status(account_id: str) -> SpamStatusVerdict | None:
    """Return the cached spam-status verdict for an account, or ``None``."""
    return await asyncio.to_thread(_get_spam_status, account_id)


def _list_spam_statuses() -> dict[str, SpamStatusVerdict]:
    with _get_engine().connect() as connection:
        rows = connection.execute(select(_account_spam_status)).mappings().all()
    return {
        str(row["account_id"]): _row_to_spam_status(cast("Mapping[str, object]", row))
        for row in rows
    }


async def list_spam_statuses() -> dict[str, SpamStatusVerdict]:
    """Return every cached spam-status verdict keyed by ``account_id`` (one query)."""
    return await asyncio.to_thread(_list_spam_statuses)


def _list_spam_statuses_by_ids(account_ids: list[str]) -> dict[str, SpamStatusVerdict]:
    if not account_ids:
        return {}
    statement = select(_account_spam_status).where(
        _account_spam_status.c.account_id.in_(account_ids),
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return {
        str(row["account_id"]): _row_to_spam_status(cast("Mapping[str, object]", row))
        for row in rows
    }


async def list_spam_statuses_by_ids(account_ids: list[str]) -> dict[str, SpamStatusVerdict]:
    """Cached spam-status verdicts for a set of accounts keyed by ``account_id``."""
    return await asyncio.to_thread(_list_spam_statuses_by_ids, account_ids)


def _upsert_spam_status(verdict: SpamStatusVerdict) -> SpamStatusVerdict:
    values = {
        "status": verdict.status,
        "detail": verdict.detail,
        "checked_at": verdict.checked_at,
    }
    with _get_engine().begin() as connection:
        updated = connection.execute(
            update(_account_spam_status)
            .where(_account_spam_status.c.account_id == verdict.account_id)
            .values(**values),
        )
        if updated.rowcount == 0:
            connection.execute(
                insert(_account_spam_status).values(account_id=verdict.account_id, **values),
            )
    return verdict


async def upsert_spam_status(verdict: SpamStatusVerdict) -> SpamStatusVerdict:
    """Insert or update one account's cached spam-status verdict."""
    return await asyncio.to_thread(_upsert_spam_status, verdict)
