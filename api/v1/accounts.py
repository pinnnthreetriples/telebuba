"""Accounts endpoints — thin routes over ``services.accounts``.

Reads return ``Page[AccountRead]`` / ``AccountRead`` (locale-neutral codes +
ISO timestamps; the SPA localizes). Writes are the actions the Accounts screen
drives: session check, profile update, delete, and the two multipart uploads
(tdata import, profile photo).
"""

from __future__ import annotations

import base64
import binascii
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi import status as http_status

from schemas.accounts import (
    AccountCheckRequest,
    AccountProfileUpdateRequest,
    AccountRead,
    AccountSessionFileImport,
)
from schemas.api import Page
from schemas.phone_login import PhoneCodeRequestResult, SubmitCodeRequest
from schemas.profile_media import (
    AccountProfileMusicRemove,
    AccountProfileMusicUpload,
    AccountProfilePhotoRemove,
    AccountProfilePhotoUpload,
    AccountProfileView,
    AccountStoryRemove,
    AccountStoryUpload,
    MusicRemoveRequest,
    PhotoRemoveRequest,
    StoryMediaKind,
    StoryPrivacyPreset,
    StoryRemoveRequest,
)
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


def _decode_ref(value: str) -> bytes:
    """Decode a base64 ``file_reference`` from the profile view, or 400."""
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="invalid file_reference",
        ) from exc


@router.get(
    "/accounts/{account_id}/profile-snapshot",
    response_model=AccountProfileView,
    operation_id="getAccountProfileSnapshot",
)
async def get_account_profile_snapshot(
    account_id: str,
    refresh: Annotated[bool, Query()] = False,  # noqa: FBT002 - refresh flag
) -> AccountProfileView:
    """Live profile (name / bio / photos / stories / music) for the edit modal.

    ``refresh=true`` (the modal's «Обновить» button) bypasses the read cache and
    re-pulls from Telegram.
    """
    return await accounts.account_profile_view(account_id, force_refresh=refresh)


@router.post(
    "/accounts/{account_id}/story",
    response_model=ActionResult,
    operation_id="postAccountStory",
)
async def post_account_story(  # noqa: PLR0913 - one Form param per story field
    account_id: str,
    file: Annotated[UploadFile, File()],
    media_kind: Annotated[StoryMediaKind, Form()] = "image",
    caption: Annotated[str | None, Form()] = None,
    privacy_preset: Annotated[StoryPrivacyPreset, Form()] = "contacts",
    protect_content: Annotated[bool, Form()] = False,  # noqa: FBT002 - multipart form field
) -> ActionResult:
    content = await file.read()
    upload = AccountStoryUpload(
        account_id=account_id,
        filename=file.filename or "story",
        content=content,
        media_kind=media_kind,
        caption=caption,
        privacy_preset=privacy_preset,
        protect_content=protect_content,
    )
    try:
        return await accounts.post_account_story(upload)
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/accounts/{account_id}/music",
    response_model=ActionResult,
    operation_id="addAccountMusic",
)
async def add_account_music(
    account_id: str,
    file: Annotated[UploadFile, File()],
    title: Annotated[str | None, Form()] = None,
    performer: Annotated[str | None, Form()] = None,
) -> ActionResult:
    content = await file.read()
    upload = AccountProfileMusicUpload(
        account_id=account_id,
        filename=file.filename or "track",
        content=content,
        title=title,
        performer=performer,
    )
    try:
        return await accounts.add_account_profile_music(upload)
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/accounts/{account_id}/story/remove",
    response_model=ActionResult,
    operation_id="removeAccountStory",
)
async def remove_account_story(account_id: str, body: StoryRemoveRequest) -> ActionResult:
    try:
        return await accounts.remove_account_story(
            AccountStoryRemove(account_id=account_id, story_id=body.story_id),
        )
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/accounts/{account_id}/music/remove",
    response_model=ActionResult,
    operation_id="removeAccountMusic",
)
async def remove_account_music(account_id: str, body: MusicRemoveRequest) -> ActionResult:
    remove = AccountProfileMusicRemove(
        account_id=account_id,
        file_id=body.file_id,
        access_hash=body.access_hash,
        file_reference=_decode_ref(body.file_reference),
    )
    try:
        return await accounts.remove_account_profile_music(remove)
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/accounts/{account_id}/photo/remove",
    response_model=ActionResult,
    operation_id="removeAccountPhoto",
)
async def remove_account_photo(account_id: str, body: PhotoRemoveRequest) -> ActionResult:
    remove = AccountProfilePhotoRemove(
        account_id=account_id,
        photo_id=body.photo_id,
        access_hash=body.access_hash,
        file_reference=_decode_ref(body.file_reference),
    )
    try:
        return await accounts.remove_account_profile_photo(remove)
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
