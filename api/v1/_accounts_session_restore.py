"""Restore an account's session file from a vault copy, and read what copies exist.

Split-sibling of ``accounts.py`` (same pattern as ``_accounts_devices.py``); mounted onto the
accounts router via ``include_router``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path

from api.errors import SERVICE_ERRORS
from api.v1._errors import service_errors_to_http
from schemas.accounts import _ACCOUNT_ID_PATTERN, AccountRead
from schemas.session_restore import SessionCopies
from services.accounts.session_restore import restore_account_session, session_copies_of

session_restore_router = APIRouter()

_AccountId = Annotated[str, Path(min_length=1, pattern=_ACCOUNT_ID_PATTERN)]


@session_restore_router.get(
    "/accounts/{account_id}/session/copies",
    response_model=SessionCopies,
    operation_id="getAccountSessionCopies",
    responses=SERVICE_ERRORS,
)
async def get_account_session_copies(account_id: _AccountId) -> SessionCopies:
    """How many vault copies the account's own session has and when the newest was made."""
    with service_errors_to_http():
        return await session_copies_of(account_id)


@session_restore_router.post(
    "/accounts/{account_id}/session/restore",
    response_model=AccountRead,
    operation_id="restoreAccountSession",
    responses=SERVICE_ERRORS,
)
async def restore_account_session_route(account_id: _AccountId) -> AccountRead:
    """Put the newest vault copy back when the account's session file is missing or keyless.

    Refused with a stable code (``session_restore_file_present``, ``session_vault_off``,
    ``session_restore_no_copy``); never over a file that holds a key.
    """
    with service_errors_to_http():
        return await restore_account_session(account_id)
