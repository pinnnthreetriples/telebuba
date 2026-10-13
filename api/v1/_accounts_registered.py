"""The account's real Telegram registration date (Прогрев 2.0, item 4.8).

Split-sibling of ``accounts.py``, mounted onto the accounts router like
``_accounts_twofa.py``. The date is the operator's; warming opens DMs a day earlier
for an account older than half a year and starts a year-old one in «warming» on day 2.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path

from api.errors import SERVICE_ERRORS
from api.v1._errors import service_errors_to_http
from schemas.accounts import _ACCOUNT_ID_PATTERN, AccountRead, AccountRegisteredUpdate
from services import accounts

registered_router = APIRouter()


@registered_router.put(
    "/accounts/{account_id}/registered-at",
    response_model=AccountRead,
    operation_id="setAccountRegisteredAt",
    responses=SERVICE_ERRORS,
)
async def set_account_registered_at(
    account_id: Annotated[str, Path(min_length=1, pattern=_ACCOUNT_ID_PATTERN)],
    body: AccountRegisteredUpdate,
) -> AccountRead:
    """Set (or clear, with ``null``) when the account was registered in Telegram."""
    with service_errors_to_http():
        return await accounts.set_registered_at(account_id, body.registered_at)
