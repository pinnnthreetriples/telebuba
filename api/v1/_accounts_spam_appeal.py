"""The semi-automatic appeal of a spam block (docs/spam-intel.md section 5.12).

A HUMAN does the whole appeal in the account's own Telegram Web window. These routes only
open that window on @SpamBot's dialog, record that the operator says an appeal was filed, and
show the record. None of them takes a body, a query value or a path value that could become
text sent to @SpamBot: the open route names its target by a fixed word in code, and the
"filed" route reads the operator from the session. Nothing here presses a button, answers the
bot or passes its captcha (pinned by ``tests/services/spam_intel/test_appeal_hard_limit.py``).

Split-sibling of ``accounts.py`` (its own paths under ``/accounts``), mounted by ``api.v1``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from api.deps import get_current_user
from api.errors import SERVICE_ERRORS
from api.v1._errors import service_errors_to_http
from schemas.accounts import _ACCOUNT_ID_PATTERN, OpenWebResult
from schemas.auth import (
    UserRead,  # noqa: TC001 - FastAPI resolves dependency annotations at runtime
)
from schemas.spam_appeals import SpamAppealList, SpamAppealState
from services import accounts, spam_intel

spam_appeal_router = APIRouter(tags=["accounts"])

_AccountId = Annotated[str, Path(min_length=1, pattern=_ACCOUNT_ID_PATTERN)]


@spam_appeal_router.get(
    "/accounts/spam-appeals",
    response_model=SpamAppealList,
    operation_id="listSpamAppeals",
)
async def list_spam_appeals() -> SpamAppealList:
    """The open appeals of every account, with the time each is re-checked."""
    return SpamAppealList(items=await spam_intel.open_appeal_states())


@spam_appeal_router.get(
    "/accounts/{account_id}/spam-appeal",
    response_model=SpamAppealState,
    operation_id="getSpamAppeal",
    responses=SERVICE_ERRORS,
)
async def get_spam_appeal(account_id: _AccountId) -> SpamAppealState:
    """What the card shows: the newest appeal (none, open or closed) and its re-check time."""
    with service_errors_to_http():
        await accounts.require_account(account_id)
        return await spam_intel.appeal_state(account_id)


@spam_appeal_router.post(
    "/accounts/{account_id}/spam-appeal/open",
    response_model=OpenWebResult,
    operation_id="openSpamAppealWindow",
    responses=SERVICE_ERRORS,
)
async def open_spam_appeal_window(account_id: _AccountId) -> OpenWebResult:
    """Open the account's own web window on @SpamBot's dialog (the operator appeals by hand).

    The account's proxy and fingerprint, refused without a proxy exactly like the plain open.
    If the dialog does not open, the window is still on Telegram Web and the operator searches
    for the bot there.
    """
    with service_errors_to_http():
        await accounts.require_account(account_id)
        return await accounts.open_account_web(account_id, target="spambot")


@spam_appeal_router.post(
    "/accounts/{account_id}/spam-appeal/filed",
    response_model=SpamAppealState,
    operation_id="markSpamAppealFiled",
    responses=SERVICE_ERRORS,
)
async def mark_spam_appeal_filed(
    account_id: _AccountId,
    user: Annotated[UserRead, Depends(get_current_user)],
) -> SpamAppealState:
    """Record that the signed-in operator filed the appeal by hand.

    Nothing is inferred from the window. A second press while the appeal is open changes
    nothing; a clean account has no block to appeal (400).
    """
    with service_errors_to_http():
        await accounts.require_account(account_id)
        return await spam_intel.record_appeal_filed(account_id, user.username)
