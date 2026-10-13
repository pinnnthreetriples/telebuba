"""The account's real Telegram registration date, as the operator gives it (item 4.8).

Optional at upload and editable on the account card. Only the operator's date is
stored (decision Q15): an estimate from the account id is a hint the SPA shows, never
a value warming acts on. Warming reads it through ``services.warming._phases``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.db import fetch_account
from core.repositories._accounts_registered import store_account_registered_at
from services.accounts._result import AccountNotFoundError

if TYPE_CHECKING:
    from datetime import date

    from schemas.accounts import AccountRead


async def set_registered_at(account_id: str, registered_at: date | None) -> AccountRead:
    """Store (or clear) the date and return the account.

    The range (Telegram's launch to today) is the request model's ``RegisteredDate``.
    """
    value = registered_at.isoformat() if registered_at is not None else None
    if not await store_account_registered_at(account_id, value):
        raise AccountNotFoundError(account_id)
    account = await fetch_account(account_id)
    if account is None:
        raise AccountNotFoundError(account_id)
    return account
