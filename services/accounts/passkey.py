"""Telegram passkeys for the accounts domain — add one, sign back in with it, remove it.

Why this exists: an account whose session Telegram revoked can otherwise only be
re-authorized by an SMS code, and a rented number is often gone by then. A passkey
signs the account back in without one (plus the stored 2FA password).

The private key is a non-exportable key in the vault (``core.passkey_vault``); the
``account_passkeys`` row only says which vault key signs for which Telegram
credential. Order matters on both ends: the vault key exists before Telegram is asked
to trust it, and the row is written only after Telegram did; removal asks Telegram
first and deletes the vault key last, so a half-done removal never leaves Telegram
trusting a key that no longer exists.

The gateway functions are imported at module scope so tests monkeypatch them at
``services.accounts.passkey.<name>`` (the convention :mod:`.login` documents).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from core.db import fetch_account, update_account_from_session_check
from core.logging import log_event
from core.passkey_vault import (
    PasskeyVaultError,
    create_signing_key,
    delete_signing_key,
    passkey_vault_configured,
    vault_key_name,
)
from core.repositories._accounts_passkey import (
    delete_account_passkey,
    fetch_account_passkey,
    save_account_passkey,
)
from core.telegram_client import (
    PasskeyGatewayError,
    delete_passkey,
    login_with_passkey,
    register_passkey,
)
from core.webauthn_passkey import credential_id_for
from schemas.passkey import AccountPasskeyView, StoredPasskey
from services.accounts._result import AccountActionError, AccountNotFoundError
from services.inbox_runtime import start_account_inbox

# One lock per account, never freed (bounded by the accounts ever touched): a
# double click must not register two passkeys or race a removal.
_PASSKEY_LOCKS: dict[str, asyncio.Lock] = {}


def _passkey_lock(account_id: str) -> asyncio.Lock:
    return _PASSKEY_LOCKS.setdefault(account_id, asyncio.Lock())


def _view(account_id: str, passkey: StoredPasskey | None) -> AccountPasskeyView:
    return AccountPasskeyView(
        account_id=account_id,
        has_passkey=passkey is not None,
        created_at=passkey.created_at if passkey else None,
        vault_configured=passkey_vault_configured(),
    )


def _gateway_error(exc: PasskeyGatewayError | PasskeyVaultError) -> AccountActionError:
    retry = exc.retry_after_seconds if isinstance(exc, PasskeyGatewayError) else None
    return AccountActionError(exc.code, retry_after_seconds=retry)


async def _require_account(account_id: str) -> None:
    if await fetch_account(account_id) is None:
        raise AccountNotFoundError(account_id)


def _require_vault() -> None:
    if not passkey_vault_configured():
        msg = "passkey_vault_disabled"
        raise AccountActionError(msg)


async def read_account_passkey(account_id: str) -> AccountPasskeyView:
    await _require_account(account_id)
    return _view(account_id, await fetch_account_passkey(account_id))


async def create_account_passkey(account_id: str) -> AccountPasskeyView:
    """Give the account a passkey whose private key lives in the vault."""
    await _require_account(account_id)
    _require_vault()
    async with _passkey_lock(account_id):
        if await fetch_account_passkey(account_id) is not None:
            msg = "passkey_exists"
            raise AccountActionError(msg)
        key_name = vault_key_name(account_id)
        try:
            # The vault key survives a failed attempt, so a retry derives the same id.
            public_key = await create_signing_key(key_name)
            credential_id = credential_id_for(public_key)
            handle = await register_passkey(
                account_id, public_key_pem=public_key, credential_id=credential_id
            )
        except (PasskeyGatewayError, PasskeyVaultError) as exc:
            await log_event(
                "WARNING", "passkey_create_failed", account_id=account_id, extra={"code": exc.code}
            )
            raise _gateway_error(exc) from exc
        passkey = StoredPasskey(
            account_id=account_id,
            credential_id=credential_id,
            user_handle=handle,
            vault_key_name=key_name,
            created_at=datetime.now(UTC).isoformat(),
        )
        await save_account_passkey(passkey)
    await log_event("INFO", "passkey_created", account_id=account_id)
    return _view(account_id, passkey)


async def relogin_with_passkey(account_id: str) -> AccountPasskeyView:
    """Sign the account's session back in with its passkey and mark it alive."""
    account = await fetch_account(account_id)
    if account is None:
        raise AccountNotFoundError(account_id)
    _require_vault()
    passkey = await fetch_account_passkey(account_id)
    if passkey is None:
        msg = "passkey_missing"
        raise AccountActionError(msg)
    result = await login_with_passkey(passkey, account.session_name)
    if result.status != "alive":
        code = result.error_message if result.status == "unauthorized" else result.status
        await log_event(
            "WARNING", "passkey_login_failed", account_id=account_id, extra={"code": code}
        )
        raise AccountActionError(
            code or "passkey_login_failed", retry_after_seconds=result.flood_wait_seconds
        )
    saved = await update_account_from_session_check(result)
    await start_account_inbox(saved.account_id)
    await log_event("INFO", "passkey_login_success", account_id=account_id)
    return _view(account_id, passkey)


async def remove_account_passkey(
    account_id: str, *, forget_only: bool = False
) -> AccountPasskeyView:
    """Remove the passkey from Telegram, then forget it and delete its vault key.

    ``forget_only`` skips Telegram: removing there needs a live session, and a dead
    one is what a passkey is for. With the vault key gone the passkey Telegram still
    lists can never sign in again; the operator can delete it in an official app.
    """
    await _require_account(account_id)
    _require_vault()
    async with _passkey_lock(account_id):
        passkey = await fetch_account_passkey(account_id)
        if passkey is None:
            msg = "passkey_missing"
            raise AccountActionError(msg)
        try:
            if not forget_only:
                await delete_passkey(account_id, passkey.credential_id)
            await delete_account_passkey(account_id)
            await delete_signing_key(passkey.vault_key_name)
        except (PasskeyGatewayError, PasskeyVaultError) as exc:
            raise _gateway_error(exc) from exc
    await log_event(
        "INFO", "passkey_removed", account_id=account_id, extra={"forget_only": forget_only}
    )
    return _view(account_id, None)
