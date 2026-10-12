"""Account-device endpoints — add the account's second device as its own sign-in.

Split-sibling of ``accounts.py`` (same pattern as ``_accounts_privacy.py``);
mounted onto the accounts router via ``include_router``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path

from api.errors import SERVICE_ERRORS
from api.v1._errors import service_errors_to_http
from schemas.accounts import _ACCOUNT_ID_PATTERN, AccountRead
from schemas.devices import AddDeviceRequest  # noqa: TC001 - FastAPI body at runtime
from services import accounts

devices_router = APIRouter()


@devices_router.post(
    "/accounts/{account_id}/devices",
    response_model=AccountRead,
    operation_id="addAccountDevice",
    responses=SERVICE_ERRORS,
)
async def add_account_device(
    account_id: Annotated[str, Path(min_length=1, pattern=_ACCOUNT_ID_PATTERN)],
    body: AddDeviceRequest,
) -> AccountRead:
    """Sign the other device in beside the account's existing one.

    A refusal answers 400 with a stable device code; a flood wait carries its seconds.
    """
    with service_errors_to_http():
        return await accounts.add_account_device(account_id, body.device_type)
