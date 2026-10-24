"""Passkey persistence: one ``account_passkeys`` row per account.

Sync helpers on a worker thread, the ``core.repositories._accounts_twofa`` shape.
The row is written only after Telegram accepted the passkey, and removed with the
account (``core.repositories._accounts_delete``).
"""

from __future__ import annotations

import asyncio

from sqlalchemy import delete, insert, select

from core._schema_passkeys import _account_passkeys
from core.db import _get_engine
from schemas.passkey import StoredPasskey


def _fetch_account_passkey(account_id: str) -> StoredPasskey | None:
    with _get_engine().connect() as connection:
        row = connection.execute(
            select(_account_passkeys).where(_account_passkeys.c.account_id == account_id),
        ).first()
    return None if row is None else StoredPasskey.model_validate(dict(row._mapping))  # noqa: SLF001


async def fetch_account_passkey(account_id: str) -> StoredPasskey | None:
    return await asyncio.to_thread(_fetch_account_passkey, account_id)


def _save_account_passkey(passkey: StoredPasskey) -> None:
    with _get_engine().begin() as connection:
        connection.execute(insert(_account_passkeys).values(**passkey.model_dump()))


async def save_account_passkey(passkey: StoredPasskey) -> None:
    """Insert the row; a second passkey for the same account is an IntegrityError."""
    await asyncio.to_thread(_save_account_passkey, passkey)


def _delete_account_passkey(account_id: str) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            delete(_account_passkeys).where(_account_passkeys.c.account_id == account_id),
        )


async def delete_account_passkey(account_id: str) -> None:
    await asyncio.to_thread(_delete_account_passkey, account_id)
