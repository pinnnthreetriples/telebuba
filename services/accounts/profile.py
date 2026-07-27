"""Profile-field updates (name / username / bio) for the accounts domain.

``execute`` / ``execute_read`` are imported at module scope so tests can
monkeypatch ``services.accounts.profile.execute`` (same for ``execute_read``).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from core.db import fetch_account, update_account_profile_snapshot
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
    from schemas.telegram_actions import ActionResult
    from schemas.telegram_profile_snapshot import TelegramProfileSnapshot

__all__ = ["update_account_profile"]

logger = logging.getLogger(__name__)

# Refusals with nothing to reconcile, so the confirmation read below would only
# spend a second — doomed — pool connect before the real error surfaces.
# ``unavailable`` never reached Telegram at all; the two username codes come from
# the FIRST RPC of the gateway's dispatch, before anything changed; and a dead or
# deactivated session cannot serve a read either. Every other refusal can have
# half-applied (the gateway sends the username before the name/bio call).
_NO_CONFIRM_CODES: frozenset[str] = frozenset(
    {
        "username_occupied",
        "username_invalid",
        "session_dead",
        "account_deactivated",
    },
)


async def update_account_profile(data: AccountProfileUpdateRequest) -> AccountRead:
    stored = await fetch_account(data.account_id)
    result = await execute(
        data.account_id,
        UpdateProfile(
            first_name=data.first_name,
            last_name=data.last_name,
            username=_username_to_send(data.username, stored),
            bio=data.bio,
        ),
    )
    # Invalidate BEFORE raising and BEFORE the DB snapshot write: a failed or
    # partial Telegram write (e.g. name applied, username refused) can still
    # have changed server state, and a DB failure after a successful write
    # must not leave the cached snapshot stale either (#249 pattern).
    invalidate_account_profile_cache(data.account_id)
    confirmed = await _sync_confirmed_profile(data.account_id) if _should_confirm(result) else None
    raise_for_result(result)
    account = confirmed if confirmed is not None else await update_account_profile_snapshot(data)
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


def _username_to_send(requested: str | None, stored: AccountRead | None) -> str | None:
    """``None`` (leave unchanged) when the requested handle is already the stored one.

    The gateway gates ``UpdateUsernameRequest`` on ``action.username is not None``
    and the SPA ALWAYS submits the current handle as a string, so every bio-only
    save fired that RPC as a no-op (Telegram answers ``UsernameNotModifiedError``,
    which the gateway suppresses). It is also the flood-sensitive call of the pair,
    and ``update_profile`` is a ``_PROFILE_EDIT_ACTION_TYPES`` member — a FloodWait
    from it writes the sticky ``flood_wait`` status, which blocks ``start_warming``
    (readiness requires ``alive``). Comparing here keeps the gateway's
    ``is not None`` contract intact; a stale DB row only degrades to sending the
    no-op again, i.e. the previous behaviour.
    """
    if requested is None or stored is None:
        return requested
    return None if requested == (stored.username or "") else requested


def _should_confirm(result: ActionResult) -> bool:
    """Whether the live profile is worth re-reading after this action result."""
    if result.status == "ok":
        return True
    if result.status == "unavailable":
        return False
    return result.error_message not in _NO_CONFIRM_CODES


async def _sync_confirmed_profile(account_id: str) -> AccountRead | None:
    """Re-read the live profile and persist what Telegram actually holds.

    Runs on the SUCCESS path too, not just after a partial apply. ``accounts.bio``
    had exactly one writer — the operator's request — and Telegram accepts
    ``updateProfile`` while silently ignoring ``about`` on young accounts, so a
    dropped bio was stored as truth and then served from the row forever whenever a
    later live pull failed. The RPC this spends is the one the no-op
    ``UpdateUsernameRequest`` above no longer spends.

    Best-effort end to end: ANY failure here — refused read, a live value our
    schema refuses (e.g. a 4-char Fragment/NFT username), a vanished account row —
    is logged and swallowed as ``None`` so the caller surfaces the action's real
    stable-code error (and falls back to persisting the request), never a re-sync
    artefact. The row then self-heals on the next successful save.
    """
    try:
        snapshot = await execute_read(account_id, GetUserProfile())
        profile = cast("TelegramProfileSnapshot", snapshot)
        if not profile.first_name:
            return None
        # ``""`` clears per the field contract — an unset optional on Telegram
        # must clear the stale DB value, not leave it (``None`` skips the column).
        return await update_account_profile_snapshot(
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
        return None
