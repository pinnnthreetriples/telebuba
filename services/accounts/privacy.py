"""Account-privacy reads/writes for the accounts domain.

Why this exists: the dashboard uploads an avatar and a bio correctly, but other
users still see a letter placeholder and no bio when the account's own Telegram
privacy keys restrict Profile photo / Bio to contacts. These flows read those
keys and set them, per account or across the whole fleet.

``execute`` / ``execute_read`` are imported at module scope so tests can
monkeypatch ``services.accounts.privacy.execute`` (same for ``execute_read``).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, cast

from core.db import fetch_account, list_accounts
from core.telegram_client import TelegramReadError, execute, execute_read
from schemas.accounts import health_for_status
from schemas.privacy import AccountPrivacyOutcome, AccountPrivacyView, BulkPrivacyResult
from schemas.telegram_actions_privacy import GetPrivacySettings, SetPrivacySettings
from services.accounts._result import (
    AccountActionError,
    AccountNotFoundError,
    raise_for_result,
)

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.privacy import AccountPrivacyUpdateRequest
    from schemas.telegram_actions_privacy import PrivacySettingsResult

logger = logging.getLogger(__name__)

__all__ = [
    "apply_account_privacy",
    "apply_privacy_to_all_accounts",
    "read_account_privacy",
]

# Fleet-wide apply concurrency. Follows the only other per-account fan-out in the
# codebase, ``services.neurocomment.bans`` (semaphore + gather), and reuses its
# default width of 4 (``neurocomment.ban_check_concurrency``): fast enough for a
# fleet-sized sweep, narrow enough that a burst of ``account.setPrivacy`` writes
# does not trip Telegram's rate limits. A literal rather than a new config knob —
# this is a one-button operator action, not a tuned runtime loop.
_APPLY_CONCURRENCY = 4


async def _require_account(account_id: str) -> None:
    """404 guard: an unknown id must not be billed as a bad request or an outage."""
    if await fetch_account(account_id) is None:
        raise AccountNotFoundError(account_id)


def _action(request: AccountPrivacyUpdateRequest) -> SetPrivacySettings:
    """Map the API body onto the gateway action (identical ``None`` = unchanged)."""
    return SetPrivacySettings(
        profile_photo=request.profile_photo,
        bio=request.bio,
        last_seen=request.last_seen,
    )


async def read_account_privacy(account_id: str) -> AccountPrivacyView:
    """The account's three live privacy levels, or ``error`` when Telegram refused.

    Error-envelope idiom (see ``AccountProfileView``): a refused read comes back
    as a populated ``error`` with no ``settings`` so the UI still renders. An
    unknown account is a genuine 404 and does raise.
    """
    await _require_account(account_id)
    try:
        result = await execute_read(account_id, GetPrivacySettings())
    except TelegramReadError as exc:
        return AccountPrivacyView(error=exc.reason)
    return AccountPrivacyView(settings=cast("PrivacySettingsResult", result))


async def apply_account_privacy(
    account_id: str,
    request: AccountPrivacyUpdateRequest,
) -> AccountPrivacyView:
    """Apply the requested keys, then return the freshly re-read live state.

    Re-reading instead of echoing the request costs three cheap reads and saves
    the SPA a second round trip — and it is the only way to show what Telegram
    actually holds when a key did not end up where it was asked to.
    """
    await _require_account(account_id)
    raise_for_result(await execute(account_id, _action(request)))
    return await read_account_privacy(account_id)


async def apply_privacy_to_all_accounts(
    request: AccountPrivacyUpdateRequest,
) -> BulkPrivacyResult:
    """Apply the same privacy keys to EVERY account in the fleet.

    Accounts whose session is not usable are reported ``skipped`` without
    spending an RPC; per-account failures are collected into ``outcomes`` and
    never raised, so one dead account cannot abort the sweep.
    """
    action = _action(request)
    semaphore = asyncio.Semaphore(_APPLY_CONCURRENCY)

    async def _apply(account: AccountRead) -> AccountPrivacyOutcome:
        # "Usable" = the accounts-list health notion the repo already has;
        # ``health_for_status(...) == "ok"`` is exactly the ``alive`` set (see
        # services.accounts._table.list_listener_accounts). Anything else has no
        # working session, so the write would only burn an RPC to fail.
        if health_for_status(account.status) != "ok":
            return AccountPrivacyOutcome(account_id=account.account_id, status="skipped")
        async with semaphore:
            try:
                raise_for_result(await execute(account.account_id, action))
            except AccountActionError as exc:
                return AccountPrivacyOutcome(
                    account_id=account.account_id,
                    status="failed",
                    error=exc.code,
                )
            except Exception as exc:  # one bad account must not abort the sweep
                # Class name only: an unexpected exception's message is arbitrary
                # text (a transport error carries the proxy host:port, a session
                # fault its file path), and this value is an API response. The
                # full detail with traceback goes to the server log instead.
                logger.exception(
                    "fleet privacy apply failed for %s",
                    account.account_id,
                    exc_info=exc,
                )
                return AccountPrivacyOutcome(
                    account_id=account.account_id,
                    status="failed",
                    error=type(exc).__name__,
                )
        return AccountPrivacyOutcome(account_id=account.account_id, status="ok")

    fleet = (await list_accounts()).accounts
    outcomes = list(await asyncio.gather(*(_apply(account) for account in fleet)))
    return BulkPrivacyResult(
        outcomes=outcomes,
        ok=sum(1 for outcome in outcomes if outcome.status == "ok"),
        failed=sum(1 for outcome in outcomes if outcome.status == "failed"),
        skipped=sum(1 for outcome in outcomes if outcome.status == "skipped"),
    )
