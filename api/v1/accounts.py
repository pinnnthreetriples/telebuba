"""Accounts endpoints — thin routes over ``services.accounts``.

Reads return ``Page[AccountRead]`` / ``AccountRead`` (locale-neutral codes +
ISO timestamps; the SPA localizes). Writes are the actions the Accounts screen
drives: session check, profile update, delete, and the two multipart uploads
(tdata import, profile photo).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Path, Query, UploadFile
from fastapi import status as http_status

from api.errors import SERVICE_ERRORS, error_responses
from api.v1._accounts_channel_posts import channel_posts_router
from api.v1._accounts_channels import channels_router
from api.v1._accounts_media import media_router
from api.v1._accounts_privacy import privacy_router
from api.v1._errors import service_errors_to_http
from api.v1._uploads import reject_oversized_upload, staged_upload
from core.config import settings
from schemas.accounts import (
    _ACCOUNT_ID_PATTERN,
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
from services import warming as warming_service

# No router-wide ``responses``: the routes here do NOT share one error surface (a
# blanket declaration advertised 404/503 on ``/accounts/stats``, which answers
# neither). 401/422/500 arrive from the guarded mount in ``api.v1.__init__``; each
# route below adds only what its own code can raise.
router = APIRouter(tags=["accounts"])

# Path params default to an unconstrained ``str``, so a percent-encoded separator
# (``..%5C..%5Cevil``) survives routing and reaches the service layer — on the
# delete route that lands in the ``.session`` unlink. Same charset the request
# bodies already enforce, from the same constant (``schemas.profile_media``
# imports it the same way), so the two entry shapes cannot drift apart.
AccountIdPath = Annotated[str, Path(min_length=1, pattern=_ACCOUNT_ID_PATTERN)]


@router.get(
    "/accounts",
    response_model=Page[AccountRead],
    operation_id="listAccounts",
    responses=error_responses(400),
)
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


@router.post(
    "/accounts/check",
    response_model=AccountRead,
    operation_id="checkAccount",
    responses=SERVICE_ERRORS,
)
async def check_account(body: AccountCheckRequest) -> AccountRead:
    # 404 on a missing row like every sibling route — the service's own guard for
    # that case is a ``ValueError``, which the mapper below would bill as a 400
    # client fault. The guard stays in the service for its other caller (the tdata
    # import); here the route does the hard lookup, exactly as ``spam-check`` does.
    with service_errors_to_http():
        await accounts.require_account(body.account_id)
        return await accounts.check_account_session(body)


@router.post(
    "/accounts/{account_id}/spam-check",
    response_model=SpamStatusVerdict,
    operation_id="spamCheckAccount",
    responses=SERVICE_ERRORS,
)
async def spam_check_account(account_id: AccountIdPath) -> SpamStatusVerdict:
    """Re-probe @SpamBot for one account and return the fresh, cached verdict."""
    # 404 on a missing row like every sibling route. ``refresh_spam_status`` answers
    # an uncached ``unknown`` verdict instead of raising, because warming and
    # neurocomment onboarding call it too and a hard raise there would change cycle
    # behaviour — so the hard lookup lives here, not in the shared service. Kept out
    # of the docstring: that text becomes the OpenAPI ``description``.
    with service_errors_to_http():
        await accounts.require_account(account_id)
        return await spam_status.refresh_spam_status(account_id, force=True)


@router.post(
    "/accounts/start-login",
    response_model=AccountRead,
    operation_id="startPhoneLogin",
    responses=error_responses(400, 409),
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
    responses=error_responses(400),
)
async def request_login_code(account_id: AccountIdPath) -> PhoneCodeRequestResult:
    """Send a Telegram login code to the account's phone (re-auth by code)."""
    try:
        return await accounts.request_login_code(account_id)
    except accounts.PhoneLoginError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/accounts/{account_id}/submit-code",
    response_model=AccountRead,
    operation_id="submitLoginCode",
    responses=error_responses(400),
)
async def submit_login_code(account_id: AccountIdPath, body: SubmitCodeRequest) -> AccountRead:
    """Complete sign-in with the SMS code (+ optional 2FA password)."""
    try:
        return await accounts.submit_login_code(account_id, body.code, body.password)
    except accounts.PhoneLoginError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/accounts/{account_id}/logout",
    response_model=AccountRead,
    operation_id="logoutAccount",
    responses=error_responses(400),
)
async def logout_account(account_id: AccountIdPath) -> AccountRead:
    """Log the account out server-side and mark it unauthorized."""
    try:
        return await accounts.logout_account(account_id)
    except accounts.PhoneLoginError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/accounts/{account_id}/reset-session",
    response_model=AccountRead,
    operation_id="resetAccountSession",
    responses=error_responses(400),
)
async def reset_account_session(account_id: AccountIdPath) -> AccountRead:
    """Log out and wipe the local session token so the next login is clean."""
    try:
        return await accounts.reset_account_session(account_id)
    except accounts.PhoneLoginError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/accounts/profile",
    response_model=AccountRead,
    operation_id="updateAccountProfile",
    responses=SERVICE_ERRORS,
)
async def update_account_profile(body: AccountProfileUpdateRequest) -> AccountRead:
    with service_errors_to_http():
        return await accounts.update_account_profile(body)


@router.delete(
    "/accounts/{account_id}",
    status_code=http_status.HTTP_204_NO_CONTENT,
    operation_id="deleteAccount",
    responses=SERVICE_ERRORS | error_responses(409),
)
async def delete_account(account_id: AccountIdPath) -> None:
    # 404 on a missing row instead of a 204 that deleted nothing (or, before the
    # service-side guard, unlinked whatever the id resolved to).
    try:
        with service_errors_to_http():
            await accounts.remove_account(account_id)
    except warming_service.WarmingTaskNotQuiescentError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="warming task is still stopping",
        ) from exc


@router.post(
    "/accounts/import-tdata",
    response_model=TdataImportResult,
    operation_id="importAccountTdata",
    responses=SERVICE_ERRORS,
)
async def import_account_tdata(
    file: Annotated[UploadFile, File()],
    label: Annotated[str | None, Form()] = None,
) -> TdataImportResult:
    async with staged_upload(
        file,
        max_bytes=settings.profile_media.tdata_max_bytes,
        detail="tdata archive is too large",
        suffix=".zip",
    ) as content_path:
        # Keep the private temp file alive through conversion and remove it after
        # success, refusal, exception, or cancellation. No 200 MB ``bytes`` copy.
        with service_errors_to_http():
            request = TdataConvertRequest(
                filename=file.filename or "tdata.zip",
                # Preserve the normal Pydantic 422 for an explicitly empty part.
                content_path=content_path if file.size != 0 else None,
                label=label,
            )
            return await accounts.import_account_tdata(request)


@router.post(
    "/accounts/import-session",
    response_model=AccountRead,
    operation_id="importAccountSession",
    responses=SERVICE_ERRORS | error_responses(409),
)
async def import_account_session(
    file: Annotated[UploadFile, File()],
    label: Annotated[str | None, Form()] = None,
) -> AccountRead:
    reject_oversized_upload(
        file,
        max_bytes=settings.profile_media.session_max_bytes,
        detail=(f"Session file is too large (>{settings.profile_media.session_max_bytes} bytes)"),
    )
    content = await file.read()
    data = AccountSessionFileImport(
        filename=file.filename or "account.session",
        content=content,
        label=label,
    )
    # ``service_errors_to_http`` owns the residual ValueError: the request model is
    # assembled here from Form/File params, so a refused ``account_id`` reaches this
    # route as a Pydantic ``ValidationError`` and the local 400 used to answer with
    # its multi-line English prose (non-negotiable #12). It becomes the same 422
    # ``validation_error`` envelope every other route now returns. The 409 is raised
    # inside because ``HTTPException`` is no ``ValueError`` and passes it untouched.
    with service_errors_to_http():
        try:
            return await accounts.import_account_session(data)
        except accounts.SessionAlreadyExistsError as exc:
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc


# Profile-media (photo / story / music) routes live in a sibling module to keep
# this file under the size cap. Mounted last so the OpenAPI path order matches
# the pre-split single-router layout; paths are unique so order is irrelevant.
router.include_router(media_router)
# Own-channel management + channel posts (same split-sibling pattern).
router.include_router(channels_router)
router.include_router(channel_posts_router)
# Telegram privacy keys (who may see the avatar / bio / last seen).
router.include_router(privacy_router)
