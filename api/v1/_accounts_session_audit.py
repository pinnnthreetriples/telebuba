"""Session audit endpoints — the sessions Telegram lists for an account, and a fresh read.

Split-sibling of ``accounts.py`` (same pattern as ``_accounts_passkey.py``); mounted onto
the accounts router via ``include_router``. Read-only towards Telegram: nothing here ends
a session. ``GET`` answers from the saved audit and never calls Telegram; ``POST`` reads
the list again and saves it.
"""

from __future__ import annotations

from fastapi import APIRouter

from api.errors import SERVICE_ERRORS
from api.v1._errors import service_errors_to_http
from schemas.session_audit import AccountSessionAudit
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
