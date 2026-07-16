"""Accounts endpoints — thin routes over ``services.accounts``.

Reads return ``Page[AccountRead]`` / ``AccountRead`` (locale-neutral codes +
ISO timestamps; the SPA localizes). Writes are the actions the Accounts screen
drives: session check, profile update, delete, and the two multipart uploads
(tdata import, profile photo).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi import status as http_status

from api.v1._accounts_channel_posts import channel_posts_router
from api.v1._accounts_channels import channels_router
from api.v1._accounts_media import media_router
from schemas.accounts import (
    AccountCheckRequest,
    AccountProfileUpdateRequest,
    AccountRead,
    AccountSessionFileImport,
    AccountStats,
)
from schemas.api import Page
from schemas.phone_login import PhoneCodeRequestResult, StartPhoneLoginRequest, SubmitCodeRequest
from schemas.spam_status import SpamStatusVerdict
from schemas.tdata import TdataConvertRequest, TdataImportResult
from services import accounts, spam_status

router = APIRouter(tags=["accounts"])


@router.get("/accounts", response_model=Page[AccountRead], operation_id="listAccounts")
async def list_accounts(
    query: str = "",
    status: str = "all",
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page[AccountRead]:
    try:
        return await accounts.list_accounts_page(
            query=query,
            status=status,
            cursor=cursor,
            limit=limit,
        )
    except accounts.InvalidCursorError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="invalid pagination cursor",
        ) from exc


@router.get("/accounts/stats", response_model=AccountStats, operation_id="accountStats")
async def account_stats() -> AccountStats:
    """Fleet-wide status counts for the Accounts page tiles (all pages, not one)."""
    return await accounts.account_stats()


@router.post("/accounts/check", response_model=AccountRead, operation_id="checkAccount")
async def check_account(body: AccountCheckRequest) -> AccountRead:
    try:
        return await accounts.check_account_session(body)
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/accounts/{account_id}/spam-check",
    response_model=SpamStatusVerdict,
    operation_id="spamCheckAccount",
)
async def spam_check_account(account_id: str) -> SpamStatusVerdict:
    """Re-probe @SpamBot for one account and return the fresh, cached verdict."""
    return await spam_status.refresh_spam_status(account_id, force=True)


@router.post(
    "/accounts/start-login",
    response_model=AccountRead,
    operation_id="startPhoneLogin",
)
async def start_phone_login(body: StartPhoneLoginRequest) -> AccountRead:
    """Create a new account from a bare phone number, ready for request-code."""
    try:
        return await accounts.start_phone_login(body.phone, body.label)
    except accounts.SessionAlreadyExistsError as exc:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except accounts.PhoneLoginError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/accounts/{account_id}/request-code",
    response_model=PhoneCodeRequestResult,
    operation_id="requestLoginCode",
)
async def request_login_code(account_id: str) -> PhoneCodeRequestResult:
    """Send a Telegram login code to the account's phone (re-auth by code)."""
    try:
        return await accounts.request_login_code(account_id)
    except accounts.PhoneLoginError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/accounts/{account_id}/submit-code",
    response_model=AccountRead,
    operation_id="submitLoginCode",
)
async def submit_login_code(account_id: str, body: SubmitCodeRequest) -> AccountRead:
    """Complete sign-in with the SMS code (+ optional 2FA password)."""
    try:
        return await accounts.submit_login_code(account_id, body.code, body.password)
    except accounts.PhoneLoginError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/accounts/{account_id}/logout",
    response_model=AccountRead,
    operation_id="logoutAccount",
)
async def logout_account(account_id: str) -> AccountRead:
    """Log the account out server-side and mark it unauthorized."""
    try:
        return await accounts.logout_account(account_id)
    except accounts.PhoneLoginError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/accounts/{account_id}/reset-session",
    response_model=AccountRead,
    operation_id="resetAccountSession",
)
async def reset_account_session(account_id: str) -> AccountRead:
    """Log out and wipe the local session token so the next login is clean."""
    try:
        return await accounts.reset_account_session(account_id)
    except accounts.PhoneLoginError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/accounts/profile", response_model=AccountRead, operation_id="updateAccountProfile")
async def update_account_profile(body: AccountProfileUpdateRequest) -> AccountRead:
    try:
        return await accounts.update_account_profile(body)
    except accounts.AccountActionError:
        # api.errors maps it to the envelope: stable code + retry seconds in fields.
        raise
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.delete(
    "/accounts/{account_id}",
    status_code=http_status.HTTP_204_NO_CONTENT,
    operation_id="deleteAccount",
)
async def delete_account(account_id: str) -> None:
    await accounts.remove_account(account_id)


@router.post(
    "/accounts/import-tdata",
    response_model=TdataImportResult,
    operation_id="importAccountTdata",
)
async def import_account_tdata(
    file: Annotated[UploadFile, File()],
    label: Annotated[str | None, Form()] = None,
) -> TdataImportResult:
    # ponytail: reads the archive into memory; stream to a temp file + content_path
    # if multi-hundred-MB tdata archives become common.
    content = await file.read()
    request = TdataConvertRequest(
        filename=file.filename or "tdata.zip",
        content=content,
        label=label,
    )
    try:
        return await accounts.import_account_tdata(request)
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/accounts/import-session",
    response_model=AccountRead,
    operation_id="importAccountSession",
)
async def import_account_session(
    file: Annotated[UploadFile, File()],
    label: Annotated[str | None, Form()] = None,
) -> AccountRead:
    content = await file.read()
    data = AccountSessionFileImport(
        filename=file.filename or "account.session",
        content=content,
        label=label,
    )
    try:
        return await accounts.import_account_session(data)
    except accounts.SessionAlreadyExistsError as exc:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


# Profile-media (photo / story / music) routes live in a sibling module to keep
# this file under the size cap. Mounted last so the OpenAPI path order matches
# the pre-split single-router layout; paths are unique so order is irrelevant.
router.include_router(media_router)
# Own-channel management + channel posts (same split-sibling pattern).
router.include_router(channels_router)
router.include_router(channel_posts_router)
