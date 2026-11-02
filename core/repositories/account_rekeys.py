"""Re-key persistence: one ``account_rekeys`` row per account — its old authorization to end.

Sync helpers on a worker thread, the ``core.repositories.session_audits`` shape. The row is
written once the new session is in place; ``close_after`` says when the old authorization
may be tried again (Telegram refuses a session younger than a day to end others), and
``closed_at`` that it is ended. It goes with the account.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core._schema_session_audits import _account_rekeys
from core.db import _get_engine
from schemas.session_rekey import StoredRekey


def _save_rekey(rekey: StoredRekey) -> None:
    values = rekey.model_dump(exclude={"account_id"})
    statement = sqlite_insert(_account_rekeys).values(account_id=rekey.account_id, **values)
    with _get_engine().begin() as connection:
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=[_account_rekeys.c.account_id], set_=values
            )
        )


async def save_rekey(rekey: StoredRekey) -> None:
    """Insert the account's re-key, or replace its previous one."""
    await asyncio.to_thread(_save_rekey, rekey)


def _fetch_rekey(account_id: str) -> StoredRekey | None:
    with _get_engine().connect() as connection:
        row = connection.execute(
            select(_account_rekeys).where(_account_rekeys.c.account_id == account_id)
        ).first()
    return None if row is None else StoredRekey.model_validate(dict(row._mapping))  # noqa: SLF001


async def fetch_rekey(account_id: str) -> StoredRekey | None:
    return await asyncio.to_thread(_fetch_rekey, account_id)


def _list_due_rekeys(now: str) -> list[StoredRekey]:
    table = _account_rekeys
    with _get_engine().connect() as connection:
        rows = connection.execute(
            select(table)
            .where(table.c.closed_at.is_(None), table.c.close_after <= now)
            .order_by(table.c.close_after)
        ).all()
    return [StoredRekey.model_validate(dict(row._mapping)) for row in rows]  # noqa: SLF001


async def list_due_rekeys(now: str) -> list[StoredRekey]:
    """Re-keys whose old authorization may be tried again at ``now`` (ISO, oldest first)."""
    return await asyncio.to_thread(_list_due_rekeys, now)
