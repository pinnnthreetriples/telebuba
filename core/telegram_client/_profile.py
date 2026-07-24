"""Profile-field edit dispatch + edit-time account-status bookkeeping.

Split from ``_actions.py`` to keep that module under the aislop file-size
budget; behavior is unchanged. The executor routes ``UpdateProfile`` here and
calls :func:`_mark_account_status` when a frozen/flood classification should
also be reflected on the accounts list.
"""

from __future__ import annotations

import logging
from contextlib import suppress
from typing import TYPE_CHECKING

from telethon import errors
from telethon.tl.functions.account import UpdateProfileRequest, UpdateUsernameRequest

from core.db import update_account_status
from core.telegram_client._media import ProfileGatewayError

if TYPE_CHECKING:
    from telethon import TelegramClient

    from schemas.accounts import AccountStatus
    from schemas.telegram_actions import UpdateProfile

logger = logging.getLogger(__name__)

# Telethon refusal family → stable, locale-neutral code for profile-field edits
# (mirrors _channels._TELETHON_ERROR_CODES). Flood-family errors are deliberately
# NOT mapped — they must reach ``execute``'s dedicated flood-wait ladder unchanged.
_PROFILE_ERROR_CODES: tuple[tuple[type[Exception], str], ...] = (
    (errors.UsernameOccupiedError, "username_occupied"),
    (errors.UsernamePurchaseAvailableError, "username_occupied"),
    (errors.UsernameInvalidError, "username_invalid"),
    (errors.AboutTooLongError, "about_too_long"),
)

# Operator-driven profile/media edits: the only action family whose FloodWait
# marks the account ``flood_wait`` in the DB (the operator sees why the edit
# was refused on the accounts list). Warming/neurocomment ride the same
# executor, but flood there is a routine pacing event with automatic recovery —
# a sticky status would block start_warming (readiness requires ``alive``) and
# park reconcile in error on restart, so their floods never touch the status.
_PROFILE_EDIT_ACTION_TYPES: frozenset[str] = frozenset(
    {
        "update_profile",
        "set_profile_photo",
        "post_story",
        "add_profile_music",
        "remove_profile_music",
        "remove_profile_photo",
        "set_main_profile_photo",
        "remove_story",
        "toggle_story_pinned",
    },
)


async def _dispatch_update_profile(client: TelegramClient, action: UpdateProfile) -> None:
    """Field contract: ``""`` clears, ``None`` leaves unchanged (omitted from TL flags).

    The username goes FIRST: it is the fallible call (occupied/invalid handle),
    and sending it after ``UpdateProfileRequest`` used to half-apply the edit —
    name/bio already changed on Telegram while the UI reported "nothing saved"
    and the DB snapshot stayed stale.

    Known refusals are re-raised as :class:`ProfileGatewayError` so the SPA
    receives a stable code instead of Telethon's English prose.
    """
    try:
        if action.username is not None:
            # Re-sending the account's current username is a no-op, not a failure.
            with suppress(errors.UsernameNotModifiedError):
                await client(UpdateUsernameRequest(username=action.username))
        await client(
            UpdateProfileRequest(
                first_name=action.first_name,
                last_name=action.last_name,
                about=action.bio,
            ),
        )
    except errors.RPCError as exc:
        for error_cls, code in _PROFILE_ERROR_CODES:
            if isinstance(exc, error_cls):
                raise ProfileGatewayError(code) from exc
        raise


async def _mark_account_status(account_id: str, status: AccountStatus) -> None:
    """Best-effort DB status write — ``execute`` returns a result, never raises.

    SQLite can refuse under live load ("database is locked", see the note in
    ``_read.execute_read_many``); a bookkeeping failure must not replace the
    typed ``ActionResult`` contract, so it is logged and swallowed. Stdlib
    logging on purpose: ``log_event`` writes to the same contended DB.
    """
    try:
        await update_account_status(account_id, status=status)
    except Exception:  # noqa: BLE001 - bookkeeping only; the action result still stands
        logger.warning(
            "account status write failed (account_id=%s, status=%s)",
            account_id,
            status,
            exc_info=True,
        )
