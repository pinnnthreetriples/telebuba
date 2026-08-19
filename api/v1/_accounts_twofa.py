"""Cloud-password (2FA) endpoints — read the live state, set/change it, remove it.

Split-sibling of ``accounts.py`` (same pattern as ``_accounts_privacy.py``);
mounted onto the accounts router via ``include_router``.

``POST`` is the only route in the whole API whose response carries a plaintext
credential (``AccountTwoFactorCreated``), and it does so exactly once — the
operator's single chance to copy it into a password manager. It is also the only
one that sets ``Cache-Control: no-store``. ``GET`` and ``DELETE`` answer with the
boolean-only view, so neither can be replayed to read the password back.

There is deliberately NO fleet-wide route: one shared password across N accounts
turns a single leak into a fleet compromise, and a per-account sweep is a
different feature (it would have to generate, return and be recorded N times).

The recovery-email routes are TWO steps by design, never one. The protocol would
allow attaching the email in the same ``updatePasswordSettings`` that sets the
password, but the password is handed to the operator exactly once and a failure
in the email leg must not be able to cost them that. So ``POST /2fa`` stands
alone, and the email flow is retryable on top of it.
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from api.errors import SERVICE_ERRORS
from api.v1._errors import service_errors_to_http
from schemas.twofa import (
    AccountTwoFactorCreated,
    AccountTwoFactorEmailConfirmRequest,
    AccountTwoFactorEmailPending,
    AccountTwoFactorEmailRequest,
    AccountTwoFactorUpdateRequest,
    AccountTwoFactorView,
)
from services import accounts

# No tags: mounted onto the accounts router (already tagged "accounts"). Every
# route here answers the same ``SERVICE_ERRORS`` set, but it is declared per
# route rather than router-wide — ``include_router`` merges responses down
# without letting a later route subtract one.
twofa_router = APIRouter()


@twofa_router.get(
    "/accounts/{account_id}/2fa",
    response_model=AccountTwoFactorView,
    operation_id="getAccountTwofa",
    responses=SERVICE_ERRORS,
)
async def get_account_twofa(account_id: str) -> AccountTwoFactorView:
    """Live cloud-password state for one account — booleans and the public hint."""
    with service_errors_to_http():
        return await accounts.read_account_twofa(account_id)


@twofa_router.post(
    "/accounts/{account_id}/2fa",
    response_model=AccountTwoFactorCreated,
    operation_id="setAccountTwofa",
    responses=SERVICE_ERRORS,
)
async def set_account_twofa(
    account_id: str,
    body: AccountTwoFactorUpdateRequest,
    response: Response,
) -> AccountTwoFactorCreated:
    """Set or change the cloud password; the response is the only copy handed out.

    ``no-store`` on the one response in this API that carries a plaintext
    credential. A POST response is already non-cacheable per RFC 9111, so this is a
    belt for any reverse proxy in front of the app, set through the injected
    ``Response`` the auth cookies and ``GET /ready`` already use.
    """
    response.headers["Cache-Control"] = "no-store"
    with service_errors_to_http():
        return await accounts.set_account_twofa(account_id, body)


@twofa_router.delete(
    "/accounts/{account_id}/2fa",
    response_model=AccountTwoFactorView,
    operation_id="removeAccountTwofa",
    responses=SERVICE_ERRORS,
)
async def remove_account_twofa(account_id: str) -> AccountTwoFactorView:
    """Turn 2FA off using the stored password, then answer with the re-read state."""
    with service_errors_to_http():
        return await accounts.remove_account_twofa(account_id)


@twofa_router.post(
    "/accounts/{account_id}/2fa/email",
    response_model=AccountTwoFactorEmailPending,
    operation_id="setAccountTwofaEmail",
    responses=SERVICE_ERRORS,
)
async def set_account_twofa_email(
    account_id: str,
    body: AccountTwoFactorEmailRequest,
) -> AccountTwoFactorEmailPending:
    """Attach a recovery email; the response says whether a code was mailed."""
    with service_errors_to_http():
        return await accounts.set_account_twofa_email(account_id, body)


@twofa_router.post(
    "/accounts/{account_id}/2fa/email/confirm",
    response_model=AccountTwoFactorView,
    operation_id="confirmAccountTwofaEmail",
    responses=SERVICE_ERRORS,
)
async def confirm_account_twofa_email(
    account_id: str,
    body: AccountTwoFactorEmailConfirmRequest,
) -> AccountTwoFactorView:
    """Confirm the pending recovery email with the code from the letter."""
    with service_errors_to_http():
        return await accounts.confirm_account_twofa_email(account_id, body)


@twofa_router.post(
    "/accounts/{account_id}/2fa/email/resend",
    response_model=AccountTwoFactorEmailPending,
    operation_id="resendAccountTwofaEmail",
    responses=SERVICE_ERRORS,
)
async def resend_account_twofa_email(account_id: str) -> AccountTwoFactorEmailPending:
    """Mail the confirmation code again for an email that is still pending."""
    with service_errors_to_http():
        return await accounts.resend_account_twofa_email(account_id)


@twofa_router.delete(
    "/accounts/{account_id}/2fa/email",
    response_model=AccountTwoFactorView,
    operation_id="cancelAccountTwofaEmail",
    responses=SERVICE_ERRORS,
)
async def cancel_account_twofa_email(account_id: str) -> AccountTwoFactorView:
    """Abandon a pending recovery email; the cloud password stays as it is."""
    with service_errors_to_http():
        return await accounts.cancel_account_twofa_email(account_id)


@twofa_router.delete(
    "/accounts/{account_id}/2fa/email/recovery",
    response_model=AccountTwoFactorView,
    operation_id="clearAccountTwofaEmail",
    responses=SERVICE_ERRORS,
)
async def clear_account_twofa_email(account_id: str) -> AccountTwoFactorView:
    """Detach a CONFIRMED recovery email; the cloud password stays as it is.

    A separate route from ``DELETE .../2fa/email`` because the two are separate
    Telegram calls: that one cancels a verification still in flight, this one clears
    an address Telegram already accepted. Nothing but
    ``account.updatePasswordSettings`` with an empty ``email`` can do the second.
    """
    with service_errors_to_http():
        return await accounts.clear_account_twofa_email(account_id)
