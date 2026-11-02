"""Session audit endpoints — the sessions Telegram lists for an account, and ending foreign ones.

Split-sibling of ``accounts.py`` (same pattern as ``_accounts_passkey.py``); mounted onto
the accounts router via ``include_router``. ``GET`` answers from the saved audit and never
calls Telegram; ``POST .../audit`` reads the list again and saves it. The two ``close``
routes end sessions and are operator actions: one by the hashes the confirm dialog listed,
one for whatever is foreign right now (the bulk run's step). Neither can end one of OUR
sessions, whatever it is asked.
"""

from __future__ import annotations

from fastapi import APIRouter

from api.errors import SERVICE_ERRORS
from api.v1._errors import service_errors_to_http
from schemas.session_audit import AccountSessionAudit
from schemas.session_close import SessionCloseRequest, SessionCloseResult
from schemas.session_rekey import AccountRekeyView
from services import accounts

# No tags: mounted onto the accounts router (already tagged "accounts").
session_audit_router = APIRouter()


@session_audit_router.get(
    "/accounts/{account_id}/sessions",
    response_model=AccountSessionAudit,
    operation_id="getAccountSessionAudit",
    responses=SERVICE_ERRORS,
)
async def get_account_session_audit(account_id: str) -> AccountSessionAudit:
    """The account's sessions as the last audit saw them; empty before the first audit."""
    with service_errors_to_http():
        return await accounts.read_account_session_audit(account_id)


@session_audit_router.post(
    "/accounts/{account_id}/sessions/audit",
    response_model=AccountSessionAudit,
    operation_id="auditAccountSessions",
    responses=SERVICE_ERRORS,
)
async def audit_account_sessions(account_id: str) -> AccountSessionAudit:
    """Read the account's sessions from Telegram now, with ours told from the rest."""
    with service_errors_to_http():
        return await accounts.audit_account_sessions(account_id)


@session_audit_router.post(
    "/accounts/{account_id}/sessions/close",
    response_model=SessionCloseResult,
    operation_id="closeAccountSessions",
    responses=SERVICE_ERRORS,
)
async def close_account_sessions(account_id: str, body: SessionCloseRequest) -> SessionCloseResult:
    """End the named sessions that are still foreign; one outcome per session."""
    with service_errors_to_http():
        return await accounts.close_account_sessions(account_id, body.hashes)


@session_audit_router.post(
    "/accounts/{account_id}/sessions/close-foreign",
    response_model=SessionCloseResult,
    operation_id="closeAccountForeignSessions",
    responses=SERVICE_ERRORS,
)
async def close_account_foreign_sessions(account_id: str) -> SessionCloseResult:
    """Read the account's sessions and end every one that is foreign right now."""
    with service_errors_to_http():
        return await accounts.close_account_foreign_sessions(account_id)


@session_audit_router.get(
    "/accounts/{account_id}/rekey",
    response_model=AccountRekeyView,
    operation_id="getAccountRekey",
    responses=SERVICE_ERRORS,
)
async def get_account_rekey(account_id: str) -> AccountRekeyView:
    """When the account's session was last re-keyed and whether its old key is ended yet."""
    with service_errors_to_http():
        return await accounts.read_account_rekey(account_id)


@session_audit_router.post(
    "/accounts/{account_id}/rekey",
    response_model=AccountRekeyView,
    operation_id="rekeyAccountSession",
    responses=SERVICE_ERRORS,
)
async def rekey_account_session(account_id: str) -> AccountRekeyView:
    """Sign the account in again as a session of its own, then end the imported one's key.

    Never automatic. The old key is ended at once when Telegram allows it, else at the time
    the answer names (``close_after``).
    """
    with service_errors_to_http():
        return await accounts.rekey_account_session(account_id)
