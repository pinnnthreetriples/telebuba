"""Account-privacy endpoints — read the three keys, set them per account or fleet-wide.

Split-sibling of ``accounts.py`` (same pattern as ``_accounts_channels.py``);
mounted onto the accounts router via ``include_router``.

There is deliberately NO "apply to a selected subset" route: the SPA has no
multi-select, so that branch would be dead code the moment it shipped.
"""

from __future__ import annotations

from fastapi import APIRouter

from api.errors import SERVICE_ERRORS
from api.v1._errors import service_errors_to_http
from schemas.privacy import AccountPrivacyUpdateRequest, AccountPrivacyView, BulkPrivacyResult
from services import accounts

# No tags: mounted onto the accounts router (already tagged "accounts"). No
# router-wide ``responses`` either: the two per-account routes below answer the
# ``SERVICE_ERRORS`` set, but the fleet-wide one cannot — see the comment in
# ``set_all_accounts_privacy``'s body, not its docstring, which is deliberately
# short — and ``include_router`` merges responses down without letting a route
# subtract.
privacy_router = APIRouter()


@privacy_router.get(
    "/accounts/{account_id}/privacy",
    response_model=AccountPrivacyView,
    operation_id="getAccountPrivacy",
    responses=SERVICE_ERRORS,
)
async def get_account_privacy(account_id: str) -> AccountPrivacyView:
    """Live Profile photo / Bio / Last seen privacy levels for one account."""
    with service_errors_to_http():
        return await accounts.read_account_privacy(account_id)


@privacy_router.put(
    "/accounts/{account_id}/privacy",
    response_model=AccountPrivacyView,
    operation_id="setAccountPrivacy",
    responses=SERVICE_ERRORS,
)
async def set_account_privacy(
    account_id: str,
    body: AccountPrivacyUpdateRequest,
) -> AccountPrivacyView:
    """Apply the given keys, then answer with the re-read state (one round trip)."""
    with service_errors_to_http():
        return await accounts.apply_account_privacy(account_id, body)


@privacy_router.post(
    "/accounts/privacy/all",
    response_model=BulkPrivacyResult,
    operation_id="setAllAccountsPrivacy",
)
async def set_all_accounts_privacy(body: AccountPrivacyUpdateRequest) -> BulkPrivacyResult:
    """Apply the same keys to every account; unusable sessions come back skipped."""
    # No ``service_errors_to_http``, and so no ``SERVICE_ERRORS`` declared: no
    # PER-ACCOUNT failure can produce 400/404/503. The sweep catches
    # ``AccountActionError`` and then bare ``Exception`` for each account and reports
    # the reason inside ``outcomes``, so the route answers 200 even when every account
    # failed, and the three statuses described a response it does not produce.
    #
    # The mapper was NOT dead, though: ``apply_privacy_to_all_accounts`` opens with
    # ``fleet = (await list_accounts()).accounts`` outside every ``try``, and that read
    # can raise — the ``status`` column is a plain ``String`` with no CHECK constraint
    # and the repository casts it to ``AccountStatus`` without a runtime check, so a
    # corrupt row surfaces as a Pydantic ``ValidationError`` (and ``_optional_int``
    # coerces ``user_id``/``proxy_port`` with a bare ``int()``). Inside the mapper that
    # answered 422 with ``fields={"body.status": ...}`` — naming a request field that
    # does not exist — or 400 carrying raw ``int()`` prose. Without it the same fault
    # is a 500, which is the honest status for a data-integrity problem the client
    # cannot fix. That is a deliberate status change on this one seam.
    #
    # Kept out of the docstring: that text becomes the OpenAPI ``description`` and
    # ships in the generated client.
    return await accounts.apply_privacy_to_all_accounts(body)
