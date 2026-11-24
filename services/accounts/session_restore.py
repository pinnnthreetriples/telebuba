"""Restore an account's session file from the newest vault copy (``core.session_vault``).

Allowed only when the account's own file is missing or holds no key: never over a keyed one.
Runs under the account's auth lock and the pool tombstone, like the other flows that replace a
session file, then checks the session so the row says what the restored key is worth.
"""

from __future__ import annotations

from core.db import fetch_account
from core.logging import log_event
from core.session_vault import vault_enabled
from core.telegram_client import account_auth_lock, removing_client
from core.telegram_client._session_restore import (
    SessionRestoreRefusedError,
    restore_newest_session_copy,
    session_restore_state,
)
from schemas.accounts import AccountCheckRequest, AccountRead
from schemas.session_restore import SessionCopies
from services.accounts._result import AccountActionError, AccountNotFoundError
from services.accounts.sessions import check_account_session

__all__ = ["restore_account_session", "session_copies_of"]


async def _account(account_id: str) -> AccountRead:
    account = await fetch_account(account_id)
    if account is None:
        raise AccountNotFoundError(account_id)
    return account


async def session_copies_of(account_id: str) -> SessionCopies:
    """The copies the vault holds of the account's own session, and whether to offer a restore."""
    account = await _account(account_id)
    count, newest, restorable = await session_restore_state(account_id, account.session_name)
    return SessionCopies(
        vault_enabled=vault_enabled(),
        count=count,
        newest_at=newest.isoformat() if newest is not None else None,
        can_restore=restorable,
    )


async def restore_account_session(account_id: str) -> AccountRead:
    """Put the newest copy back; the account's row after a session check."""
    account = await _account(account_id)
    async with account_auth_lock(account_id), removing_client(account_id):
        try:
            version_at = await restore_newest_session_copy(account_id, account.session_name)
        except SessionRestoreRefusedError as exc:
            raise AccountActionError(exc.code) from None
    await log_event(
        "WARNING",
        "account_session_restored",
        account_id=account_id,
        extra={"version_at": version_at.isoformat()},
    )
    return await check_account_session(AccountCheckRequest(account_id=account_id))
