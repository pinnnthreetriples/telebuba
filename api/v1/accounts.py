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

from schemas.accounts import AccountCheckRequest, AccountProfileUpdateRequest, AccountRead
from schemas.api import Page
from schemas.profile_media import AccountProfilePhotoUpload
from schemas.spam_status import SpamStatusVerdict
from schemas.tdata import TdataConvertRequest, TdataImportResult
from schemas.telegram_actions import ActionResult
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


@router.post("/accounts/profile", response_model=AccountRead, operation_id="updateAccountProfile")
async def update_account_profile(body: AccountProfileUpdateRequest) -> AccountRead:
    try:
        return await accounts.update_account_profile(body)
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


@router.post("/accounts/photo", response_model=ActionResult, operation_id="setAccountPhoto")
async def set_account_photo(
    account_id: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
) -> ActionResult:
    content = await file.read()
    upload = AccountProfilePhotoUpload(
        account_id=account_id,
        filename=file.filename or "photo.jpg",
        content=content,
    )
    return await accounts.set_account_profile_photo(upload)
