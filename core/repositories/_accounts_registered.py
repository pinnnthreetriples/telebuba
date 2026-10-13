"""The account's real Telegram registration date — ``accounts.telegram_registered_at``.

Its own module for the file-size budget of ``core.repositories.accounts``. Only the
operator sets it (at upload or on the account card), so ``registered_source`` names
them; clearing the date clears the source too.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from sqlalchemy import update

from core.db import _accounts, _get_engine, _now_iso

if TYPE_CHECKING:
    from schemas.accounts import RegisteredSource


def _store(account_id: str, registered_at: str | None, source: RegisteredSource | None) -> bool:
    statement = (
        update(_accounts)
        .where(_accounts.c.account_id == account_id)
        .values(
            telegram_registered_at=registered_at,
            registered_source=source if registered_at is not None else None,
            updated_at=_now_iso(),
        )
    )
    with _get_engine().begin() as connection:
        return connection.execute(statement).rowcount > 0


async def store_account_registered_at(
    account_id: str,
    registered_at: str | None,
    source: RegisteredSource | None = "operator",
) -> bool:
    """Write (or clear, with ``None``) the registration date; False when no such account."""
    return await asyncio.to_thread(_store, account_id, registered_at, source)
