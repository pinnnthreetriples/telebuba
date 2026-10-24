"""Telegram passkey endpoints — read whether one exists, create it, sign in with it, remove it.

Split-sibling of ``accounts.py`` (same pattern as ``_accounts_twofa.py``); mounted onto
the accounts router via ``include_router``. No response carries key material: the
private key never leaves the vault, so every route answers the boolean view.
"""

from __future__ import annotations

from fastapi import APIRouter

from api.errors import SERVICE_ERRORS
from api.v1._errors import service_errors_to_http
from schemas.passkey import AccountPasskeyView
from services import accounts

# No tags: mounted onto the accounts router (already tagged "accounts").
passkey_router = APIRouter()


@passkey_router.get(
    "/accounts/{account_id}/passkey",
    response_model=AccountPasskeyView,
    operation_id="getAccountPasskey",
    responses=SERVICE_ERRORS,
)
async def get_account_passkey(account_id: str) -> AccountPasskeyView:
    """Whether the account has a passkey and whether the key vault is configured."""
    with service_errors_to_http():
        return await accounts.read_account_passkey(account_id)


@passkey_router.post(
    "/accounts/{account_id}/passkey",
    response_model=AccountPasskeyView,
    operation_id="createAccountPasskey",
    responses=SERVICE_ERRORS,
)
async def create_account_passkey(account_id: str) -> AccountPasskeyView:
    """Add a passkey to the account; its private key is created in the key vault."""
    with service_errors_to_http():
        return await accounts.create_account_passkey(account_id)


@passkey_router.post(
    "/accounts/{account_id}/passkey/login",
    response_model=AccountPasskeyView,
    operation_id="loginAccountWithPasskey",
    responses=SERVICE_ERRORS,
)
async def login_account_with_passkey(account_id: str) -> AccountPasskeyView:
    """Sign the account's session back in with its passkey (no SMS code)."""
    with service_errors_to_http():
        return await accounts.relogin_with_passkey(account_id)


@passkey_router.delete(
    "/accounts/{account_id}/passkey",
    response_model=AccountPasskeyView,
    operation_id="removeAccountPasskey",
    responses=SERVICE_ERRORS,
)
async def remove_account_passkey(account_id: str) -> AccountPasskeyView:
    """Remove the passkey from Telegram and delete its vault key."""
    with service_errors_to_http():
        return await accounts.remove_account_passkey(account_id)
