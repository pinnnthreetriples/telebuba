"""Account-privacy endpoints — read the three keys, set them per account or fleet-wide.

Split-sibling of ``accounts.py`` (same pattern as ``_accounts_channels.py``);
mounted onto the accounts router via ``include_router``.

There is deliberately NO "apply to a selected subset" route: the SPA has no
multi-select, so that branch would be dead code the moment it shipped.
"""

from __future__ import annotations

from fastapi import APIRouter

from api.v1._errors import service_errors_to_http
from schemas.privacy import AccountPrivacyUpdateRequest, AccountPrivacyView, BulkPrivacyResult
from services import accounts

# No tags: mounted onto the accounts router (already tagged "accounts").
privacy_router = APIRouter()


@privacy_router.get(
    "/accounts/{account_id}/privacy",
    response_model=AccountPrivacyView,
    operation_id="getAccountPrivacy",
)
async def get_account_privacy(account_id: str) -> AccountPrivacyView:
    """Live Profile photo / Bio / Last seen privacy levels for one account."""
    with service_errors_to_http():
        return await accounts.read_account_privacy(account_id)


@privacy_router.put(
    "/accounts/{account_id}/privacy",
    response_model=AccountPrivacyView,
    operation_id="setAccountPrivacy",
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
    with service_errors_to_http():
        return await accounts.apply_privacy_to_all_accounts(body)
