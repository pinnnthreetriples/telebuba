"""The Spam-check button and the fleet's probe warning (docs/spam-intel.md 5.10).

Split-sibling of ``accounts.py``, mounted onto the accounts router like ``_accounts_twofa.py``.
The check goes through the spam-intel gateway and its shared budget; an out-of-turn click (the
last probe is past the 30-minute window but under a day old) is refused with 409
``needs_confirmation`` unless the body confirms it.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, Path
from fastapi import status as http_status

from api.errors import SERVICE_ERRORS, error_responses
from api.v1._errors import service_errors_to_http
from schemas.accounts import _ACCOUNT_ID_PATTERN
from schemas.spam_card import SpamBudgetStatus, SpamCheckRequest, SpamCheckResult
from services import accounts, spam_intel

spam_check_router = APIRouter()

_AccountId = Annotated[str, Path(min_length=1, pattern=_ACCOUNT_ID_PATTERN)]


@spam_check_router.get(
    "/accounts/spam-budget",
    response_model=SpamBudgetStatus,
    operation_id="spamBudgetStatus",
)
async def spam_budget_status() -> SpamBudgetStatus:
    """The fleet's @SpamBot probes over the last 24 h against the caps, for the page warning."""
    return await spam_intel.budget_status()


@spam_check_router.post(
    "/accounts/{account_id}/spam-check",
    response_model=SpamCheckResult,
    operation_id="spamCheckAccount",
    responses=SERVICE_ERRORS | error_responses(409),
)
async def spam_check_account(
    account_id: _AccountId,
    body: Annotated[SpamCheckRequest | None, Body()] = None,
) -> SpamCheckResult:
    """Re-probe @SpamBot for one account within the shared probe budget; return the verdict.

    Out of turn (the last probe is past the 30-minute window but under a day old) the click is
    refused with 409 ``needs_confirmation`` unless ``confirm_out_of_turn`` is true. A held or
    refused check answers 200 with ``served_from``, ``reason`` and ``retry_at`` instead.
    """
    # 404 on a missing row like every sibling route. ``refresh_spam_status`` answers
    # an uncached ``unknown`` verdict instead of raising, because warming and
    # neurocomment onboarding call it too and a hard raise there would change cycle
    # behaviour — so the hard lookup lives here, not in the shared service. Kept out
    # of the docstring: that text becomes the OpenAPI ``description``.
    confirmed = body is not None and body.confirm_out_of_turn
    try:
        with service_errors_to_http():
            await accounts.require_account(account_id)
            return await spam_intel.check_for_operator(account_id, confirm_out_of_turn=confirmed)
    except spam_intel.OutOfTurnConfirmationError as exc:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=exc.code) from exc
