"""Spam appeals: the record that the operator filed one by hand (docs/spam-intel.md 5.12).

Sync helpers on a worker thread, the ``spam_evidence`` shape. Only ``services.spam_intel`` may
use this module (``tests/test_architecture.py`` guards its imports). At most one row per account
is OPEN (``closed_at IS NULL``); the partial unique index ``ux_spam_appeals_open`` holds that
even when two requests race, and filing again while one is open returns the open row unchanged.

Nothing here stores text a person typed into @SpamBot: the row has the operator's username, two
instants, the verdict at filing and the day the limit ends.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from core._schema_spam_appeals import _spam_appeals
from core.db import _get_engine
from schemas.spam_appeals import SpamAppeal

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.engine import Connection

_T = _spam_appeals


def _iso(moment: datetime) -> str:
    """Fixed-width UTC text, so string order is time order."""
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def _appeal(mapping: Mapping[str, Any]) -> SpamAppeal:
    values = dict(mapping)
    values["until_day"] = (
        None if values["until_day"] is None else date.fromisoformat(str(values["until_day"]))
    )
    return SpamAppeal.model_validate(values)


def _open_row(connection: Connection, account_id: str) -> SpamAppeal | None:
    row = (
        connection.execute(
            select(_T).where(_T.c.account_id == account_id, _T.c.closed_at.is_(None))
        )
        .mappings()
        .first()
    )
    return None if row is None else _appeal(cast("Mapping[str, Any]", row))


def _fetch_open(account_id: str) -> SpamAppeal | None:
    with _get_engine().connect() as connection:
        return _open_row(connection, account_id)


async def fetch_open_appeal(account_id: str) -> SpamAppeal | None:
    """The account's open appeal, or ``None``."""
    return await asyncio.to_thread(_fetch_open, account_id)


def _fetch_latest(account_id: str) -> SpamAppeal | None:
    statement = (
        select(_T)
        .where(_T.c.account_id == account_id)
        .order_by(_T.c.filed_at.desc(), _T.c.id.desc())
        .limit(1)
    )
    with _get_engine().connect() as connection:
        row = connection.execute(statement).mappings().first()
    return None if row is None else _appeal(cast("Mapping[str, Any]", row))


async def fetch_latest_appeal(account_id: str) -> SpamAppeal | None:
    """The account's newest appeal, open or closed (what its card shows), or ``None``."""
    return await asyncio.to_thread(_fetch_latest, account_id)


def _list_open() -> list[SpamAppeal]:
    statement = select(_T).where(_T.c.closed_at.is_(None)).order_by(_T.c.filed_at, _T.c.id)
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [_appeal(cast("Mapping[str, Any]", row)) for row in rows]


async def list_open_appeals() -> list[SpamAppeal]:
    """Every open appeal, oldest first."""
    return await asyncio.to_thread(_list_open)


def _list_latest(account_ids: list[str]) -> dict[str, SpamAppeal]:
    if not account_ids:
        return {}
    newest = (
        select(func.max(_T.c.id)).where(_T.c.account_id.in_(account_ids)).group_by(_T.c.account_id)
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(select(_T).where(_T.c.id.in_(newest))).mappings().all()
    return {str(row["account_id"]): _appeal(cast("Mapping[str, Any]", row)) for row in rows}


async def list_latest_appeals(account_ids: list[str]) -> dict[str, SpamAppeal]:
    """The newest appeal (open or closed) of each given account that has one, in one query."""
    return await asyncio.to_thread(_list_latest, account_ids)


def _file(
    account_id: str, filed_at: datetime, filed_by: str, verdict: str, until_day: date | None
) -> tuple[SpamAppeal, bool]:
    values = {
        "account_id": account_id,
        "filed_at": _iso(filed_at),
        "filed_by": filed_by,
        "verdict": verdict,
        "until_day": None if until_day is None else until_day.isoformat(),
    }
    for _attempt in range(2):
        with _get_engine().begin() as connection:
            if (existing := _open_row(connection, account_id)) is not None:
                return existing, False
            try:
                connection.execute(_T.insert().values(**values))
            except IntegrityError:
                continue  # a racing request filed first: read its row on the next pass
            created = _open_row(connection, account_id)
        if created is not None:
            return created, True
    msg = "the open spam appeal could not be read back"
    raise RuntimeError(msg)


async def file_appeal(
    account_id: str,
    *,
    filed_at: datetime,
    filed_by: str,
    verdict: str,
    until_day: date | None,
) -> tuple[SpamAppeal, bool]:
    """Record an appeal; ``(row, True)`` when new, ``(open row, False)`` when one was open."""
    return await asyncio.to_thread(_file, account_id, filed_at, filed_by, verdict, until_day)


def _close(account_id: str, closed_at: datetime, kind: str) -> bool:
    with _get_engine().begin() as connection:
        return (
            connection.execute(
                update(_T)
                .where(_T.c.account_id == account_id, _T.c.closed_at.is_(None))
                .values(closed_at=_iso(closed_at), closed_by_kind=kind)
            ).rowcount
            > 0
        )


async def close_open_appeal(account_id: str, *, closed_at: datetime, kind: str) -> bool:
    """Close the account's open appeal; ``True`` when there was one."""
    return await asyncio.to_thread(_close, account_id, closed_at, kind)


def delete_account_appeals(connection: Connection, account_id: str) -> None:
    """The account's appeals go with it (called inside the account delete)."""
    connection.execute(delete(_T).where(_T.c.account_id == account_id))
