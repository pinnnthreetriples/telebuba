"""Mobile-container endpoints — open a mobile account in its local Android container.

Split-sibling of ``accounts.py``; mounted onto the accounts router via ``include_router``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path
from fastapi import status as http_status

from api.errors import SERVICE_ERRORS, error_responses
from api.v1._errors import service_errors_to_http
from core.config import settings
from schemas.accounts import _ACCOUNT_ID_PATTERN
from schemas.emulator import EmulatorStatus, OpenMobileResult
from services import accounts

emulator_router = APIRouter()


@emulator_router.get(
    "/accounts/emulator",
    response_model=EmulatorStatus,
    operation_id="emulatorStatus",
)
async def emulator_status() -> EmulatorStatus:
    """Whether opening accounts in the Android container is switched on."""
    return EmulatorStatus(enabled=settings.emulator.enabled)


@emulator_router.post(
    "/accounts/{account_id}/open-mobile",
    response_model=OpenMobileResult,
    operation_id="openAccountMobile",
    responses=SERVICE_ERRORS | error_responses(409),
)
async def open_account_mobile(
    account_id: Annotated[str, Path(min_length=1, pattern=_ACCOUNT_ID_PATTERN)],
) -> OpenMobileResult:
    """Start the account's container and sign its Telegram app in.

    A refusal answers 400 with a stable code; a start already running for the same
    account answers 409 ``emulator_busy``.
    """
    with service_errors_to_http():
        try:
            return await accounts.open_account_mobile(account_id)
        except accounts.EmulatorBusyError as exc:
            # Caught inside: it is an ``AccountActionError`` the block would bill as 400.
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail=exc.code,
            ) from exc
