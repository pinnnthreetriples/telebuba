"""The last read of an account's cloud-password settings, for the guard's security watch.

Sync helpers on a worker thread, the ``core.repositories.account_own_authorizations`` shape.
The row goes with the account (``core.repositories._accounts_delete``).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core._schema_security_snapshots import _account_security_snapshots
from core.db import _get_engine
from schemas.security_snapshot import SecuritySnapshot

_TABLE = _account_security_snapshots


def _fetch(account_id: str) -> SecuritySnapshot | None:
    with _get_engine().connect() as connection:
        row = connection.execute(select(_TABLE).where(_TABLE.c.account_id == account_id)).first()
    return None if row is None else SecuritySnapshot.model_validate(dict(row._mapping))  # noqa: SLF001


async def fetch_security_snapshot(account_id: str) -> SecuritySnapshot | None:
    """The stored snapshot, or ``None`` when none was taken (or it was forgotten)."""
    return await asyncio.to_thread(_fetch, account_id)


def _save(account_id: str, snapshot: SecuritySnapshot) -> None:
    values = {
        **snapshot.model_dump(exclude={"checked_at"}),
        "checked_at": datetime.now(UTC).isoformat(),
    }
    statement = sqlite_insert(_TABLE).values(account_id=account_id, **values)
    statement = statement.on_conflict_do_update(index_elements=["account_id"], set_=values)
    with _get_engine().begin() as connection:
        connection.execute(statement)


async def save_security_snapshot(account_id: str, snapshot: SecuritySnapshot) -> None:
    """Store ``snapshot`` as the account's latest, replacing the previous one."""
    await asyncio.to_thread(_save, account_id, snapshot)


def _forget(account_id: str) -> None:
    with _get_engine().begin() as connection:
        connection.execute(delete(_TABLE).where(_TABLE.c.account_id == account_id))


async def forget_security_snapshot(account_id: str) -> None:
    """Drop the snapshot, so the next read of the account is a fresh baseline."""
    await asyncio.to_thread(_forget, account_id)
