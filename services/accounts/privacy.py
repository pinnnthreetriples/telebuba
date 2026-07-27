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
from core.telegram_client import (
    TelegramAccountNotFoundError,
    TelegramReadError,
    execute,
    execute_read,
)
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

# Fleet-wide apply concurrency, MODULE level so the cap is per process and not per
# request: a semaphore built inside the coroutine would let two overlapping sweeps
# (double click, two browser tabs) reach 8 concurrent writes. Width 4 follows the
# per-account fan-outs the codebase already runs (``neurocomment.ban_check_concurrency``
# and ``warming.cycle_concurrency``, both defaulting low); both of those are config
# fields and this is a literal, because they pace continuous runtime loops while this
# is a one-button operator action nobody will tune.
_APPLY_SEMAPHORE = asyncio.Semaphore(4)


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
    unknown account is a genuine 404 and does raise — including when the row
    disappears between the guard and the read, which the gateway reports with its
    own error type; without translating it the route would answer 500.
    """
    await _require_account(account_id)
    try:
        result = await execute_read(account_id, GetPrivacySettings())
    except TelegramReadError as exc:
        return AccountPrivacyView(error=exc.reason)
    except TelegramAccountNotFoundError as exc:
        raise AccountNotFoundError(account_id) from exc
    return AccountPrivacyView(settings=cast("PrivacySettingsResult", result))


async def apply_account_privacy(
    account_id: str,
    request: AccountPrivacyUpdateRequest,
) -> AccountPrivacyView:
    """Apply the requested keys, then return the freshly re-read live state.

    Re-reading instead of echoing the request costs three cheap reads and saves
    the SPA a second round trip.

    It does NOT cover a partial write. ``account.setPrivacy`` is one call per key,
    so a refusal on the second key leaves the first already changed on Telegram —
    and ``raise_for_result`` fires before the re-read, so the caller gets the error
    and no fresh state. The SPA is what closes that: it re-reads on a failed write.
    Ordering the calls differently would not help; there is no key whose refusal is
    more likely, unlike ``_dispatch_update_profile``, where the username is the one
    fallible call and therefore goes first.
    """
    await _require_account(account_id)
    raise_for_result(await execute(account_id, _action(request)))
    return await read_account_privacy(account_id)


async def apply_privacy_to_all_accounts(
    request: AccountPrivacyUpdateRequest,
) -> BulkPrivacyResult:
    """Apply the same privacy keys to EVERY account in the fleet.

    Only PERMANENTLY dead accounts are skipped, and a skip always says which
    status caused it. The obvious filter — ``health_for_status(...) == "ok"``,
    i.e. status ``alive`` — is wrong for the one scenario this action exists for:
    a farm imported minutes ago is all ``new`` until something checks it, so that
    filter reported ``ok: 0, skipped: 120`` on exactly the fleet the operator was
    trying to open up. ``new``, ``flood_wait``, ``network_error`` and
    ``proxy_error`` all have a session worth trying; a flood may have expired
    hours ago, and the status is sticky until the next check. The per-account
    route filters nothing at all, so this also stops the fleet button refusing
    accounts the single-account button writes happily.

    Per-account failures are collected into ``outcomes`` and never raised, so one
    dead account cannot abort the sweep. A failed outcome also carries ``applied``:
    the keys that already changed before the refusal, which the single-account route
    leaves to the SPA's re-read and this one has no equivalent for.
    """
    action = _action(request)

    async def _apply(account: AccountRead) -> AccountPrivacyOutcome:
        if health_for_status(account.status) == "fail":
            # unauthorized / session_error / account_error / frozen — no session
            # to write with, so the RPC would only be burned. The status rides
            # along as the reason: a bare count reads as "your sessions are broken".
            return AccountPrivacyOutcome(
                account_id=account.account_id,
                status="skipped",
                error=account.status,
            )
        async with _APPLY_SEMAPHORE:
            try:
                raise_for_result(await execute(account.account_id, action))
            except AccountActionError as exc:
                # ``applied`` is the fleet path's only signal about a PARTIAL write:
                # setPrivacy is one call per key with no rollback, and unlike the
                # single-account route there is no SPA re-read per account to close
                # the gap. Without it an account whose photo key landed before the
                # bio key flooded was reported "failed" — i.e. unchanged — while its
                # avatar was already public.
                return AccountPrivacyOutcome(
                    account_id=account.account_id,
                    status="failed",
                    error=exc.code,
                    applied=exc.applied_privacy_keys or [],
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
