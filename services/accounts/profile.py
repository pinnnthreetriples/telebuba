"""Profile-field updates (name / username / bio) for the accounts domain.

``execute`` / ``execute_read`` are imported at module scope so tests can
monkeypatch ``services.accounts.profile.execute`` (same for ``execute_read``).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from core.db import update_account_profile_snapshot
from core.logging import log_event
from core.telegram_client import execute, execute_read

# AccountProfileUpdateRequest is constructed at runtime by the re-sync path,
# so it cannot live in the TYPE_CHECKING block.
from schemas.accounts import PROFILE_BIO_MAX_LENGTH, AccountProfileUpdateRequest
from schemas.telegram_actions import GetUserProfile, UpdateProfile
from services.accounts._result import raise_for_result
from services.accounts.profile_read import invalidate_account_profile_cache

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.telegram_profile_snapshot import TelegramProfileSnapshot

__all__ = ["update_account_profile"]

logger = logging.getLogger(__name__)


async def update_account_profile(data: AccountProfileUpdateRequest) -> AccountRead:
    result = await execute(
        data.account_id,
        UpdateProfile(
            first_name=data.first_name,
            last_name=data.last_name,
            username=data.username,
            bio=data.bio,
        ),
    )
    # Invalidate BEFORE raising and BEFORE the DB snapshot write: a failed or
    # partial Telegram write (e.g. name applied, username refused) can still
    # have changed server state, and a DB failure after a successful write
    # must not leave the cached snapshot stale either (#249 pattern).
    invalidate_account_profile_cache(data.account_id)
    if result.status != "ok" and data.username is not None:
        # The gateway sends the username FIRST (see _dispatch_update_profile),
        # so a failed follow-up ``UpdateProfileRequest`` can leave an already
        # applied username on Telegram while the DB row still holds the old
        # one. Best-effort: copy the confirmed fields into the DB snapshot
        # before surfacing the refusal.
        await _sync_confirmed_profile(data.account_id)
    raise_for_result(result)
    account = await update_account_profile_snapshot(data)
    await log_event(
        "INFO",
        "account_profile_updated",
        account_id=data.account_id,
        extra={
            "has_last_name": data.last_name is not None,
            "has_username": data.username is not None,
            "has_bio": data.bio is not None,
        },
    )
    return account


async def _sync_confirmed_profile(account_id: str) -> None:
    """Re-read the live profile and persist what Telegram actually holds.

    Failure-path only (never costs the happy path an RPC), and best-effort end
    to end: ANY failure here — refused read, a live value our schema refuses
    (e.g. a 4-char Fragment/NFT username), a vanished account row — is logged
    and swallowed so the caller surfaces the action's real stable-code error,
    never a re-sync artefact. The row then self-heals on the next session check.
    """
    try:
        snapshot = await execute_read(account_id, GetUserProfile())
        profile = cast("TelegramProfileSnapshot", snapshot)
        if not profile.first_name:
            return
        # ``""`` clears per the field contract — an unset optional on Telegram
        # must clear the stale DB value, not leave it (``None`` skips the column).
        await update_account_profile_snapshot(
            AccountProfileUpdateRequest(
                account_id=account_id,
                first_name=profile.first_name,
                last_name=profile.last_name or "",
                username=profile.username or "",
                # A premium account's live bio can exceed our 70-char schema cap;
                # clamp so the snapshot write can't fail validation mid-sync.
                bio=(profile.bio or "")[:PROFILE_BIO_MAX_LENGTH],
            ),
        )
    except Exception:  # noqa: BLE001 - best-effort sync; the original error must win
        logger.debug(
            "confirmed-profile re-sync skipped (account_id=%s)",
            account_id,
            exc_info=True,
        )
