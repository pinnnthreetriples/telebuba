"""QR-code login endpoints — placeholder account, show the QR, poll, 2FA, cancel.

Split-sibling of ``accounts.py`` (same pattern as ``_accounts_passkey.py``); mounted
onto the accounts router via ``include_router``. The response carries the QR as an
SVG data URI and the saved account once signed in; the login token behind the QR
and the 2FA password never come back.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path
from fastapi import status as http_status

from api.errors import SERVICE_ERRORS, error_responses
from api.v1._errors import service_errors_to_http
from schemas.accounts import _ACCOUNT_ID_PATTERN, AccountRead
from schemas.qr_login import QrLoginView, StartQrLoginRequest, SubmitQrPasswordRequest
from services import accounts

# No tags: mounted onto the accounts router (already tagged "accounts").
qr_login_router = APIRouter()

# Same charset the accounts router enforces on its path ids (defined here, not
# imported, because ``accounts.py`` imports this module).
_AccountId = Annotated[str, Path(min_length=1, pattern=_ACCOUNT_ID_PATTERN)]
# 409: the scanned Telegram account is already in Telebuba.
_QR_ERRORS = SERVICE_ERRORS | error_responses(409)


def _qr_http_error(exc: accounts.QrLoginError) -> HTTPException:
    """409 for an account Telebuba already has, 400 for every other refusal."""
    if exc.code == "account_exists":
        return HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=exc.code)
    return HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=exc.code)


@qr_login_router.post(
    "/accounts/start-qr-login",
    response_model=AccountRead,
    operation_id="startQrLogin",
    responses=_QR_ERRORS,
)
async def start_qr_login(body: StartQrLoginRequest) -> AccountRead:
    """Create a placeholder account for a QR sign-in; nothing connects to Telegram yet."""
    with service_errors_to_http():
        try:
            return await accounts.start_qr_login(body.label, body.session_device)
        except accounts.QrLoginError as exc:
            raise _qr_http_error(exc) from exc


@qr_login_router.post(
    "/accounts/{account_id}/qr-login",
    response_model=QrLoginView,
    operation_id="beginQrLogin",
    responses=_QR_ERRORS,
)
async def begin_qr_login(account_id: _AccountId) -> QrLoginView:
    """Connect the account's session and return the first QR code (restarts an open one)."""
    with service_errors_to_http():
        try:
            return await accounts.begin_qr_login(account_id)
        except accounts.QrLoginError as exc:
            raise _qr_http_error(exc) from exc


@qr_login_router.get(
    "/accounts/{account_id}/qr-login",
    response_model=QrLoginView,
    operation_id="getQrLogin",
    responses=_QR_ERRORS,
)
async def get_qr_login(account_id: _AccountId) -> QrLoginView:
    """Where the QR sign-in stands; the account is saved the first time it reads alive."""
    with service_errors_to_http():
        try:
            return await accounts.get_qr_login(account_id)
        except accounts.QrLoginError as exc:
            raise _qr_http_error(exc) from exc


@qr_login_router.post(
    "/accounts/{account_id}/qr-login/password",
    response_model=QrLoginView,
    operation_id="submitQrLoginPassword",
    responses=_QR_ERRORS,
)
async def submit_qr_login_password(
    account_id: _AccountId,
    body: SubmitQrPasswordRequest,
) -> QrLoginView:
    """Answer the 2FA prompt of an open QR sign-in with the account's cloud password."""
    with service_errors_to_http():
        try:
            return await accounts.submit_qr_password(account_id, body.password)
        except accounts.QrLoginError as exc:
            raise _qr_http_error(exc) from exc


@qr_login_router.delete(
    "/accounts/{account_id}/qr-login",
    status_code=http_status.HTTP_204_NO_CONTENT,
    operation_id="cancelQrLogin",
)
async def cancel_qr_login(account_id: _AccountId) -> None:
    """Close an open QR sign-in; a no-op when there is none."""
    await accounts.cancel_qr_login(account_id)
