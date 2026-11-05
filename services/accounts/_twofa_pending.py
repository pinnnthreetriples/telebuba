"""The pending cloud-password candidate: staging, promotion and resolution.

A 2FA write stages its new password in ``accounts.twofa_password_pending`` BEFORE
``account.updatePasswordSettings`` is sent, so a crash or a lost reply after Telegram
applied it never leaves only the old password (the account would be lost). Sibling of
``services.accounts.twofa`` (file budget); the dependency runs one way, this module
never imports it, so the Telegram verify call is injected.

No value here may reach a log extra or an error message.
"""

from __future__ import annotations

import hashlib
import time
from typing import TYPE_CHECKING, NamedTuple

from core.config import settings
from core.db import (
    fetch_account_twofa_pair,
    promote_account_twofa_pending,
    set_account_twofa_pending,
    twofa_password_candidates,
)
from core.logging import log_event

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from schemas.telegram_actions_twofa import TwoFactorStatusResult

    # ``True`` Telegram accepts it, ``False`` Telegram refused it, ``None`` unknown.
    PasswordVerifier = Callable[[str], Awaitable[bool | None]]

__all__ = [
    "Settled",
    "promote_pending",
    "resolve_current",
    "stage_pending",
    "twofa_password_candidates",
]


async def stage_pending(account_id: str, password: str | None) -> bool:
    """Durably stage ``password`` (``None`` clears); ``False`` means do NOT send the RPC."""
    try:
        staged = await set_account_twofa_pending(account_id, password)
    except Exception:  # noqa: BLE001 - reported as a refusal; the value must not travel
        staged = False
    if not staged:
        await log_event(
            "ERROR",
            "account_twofa_pending_failed",
            account_id=account_id,
            extra={"clearing": password is None},
        )
    return staged


async def promote_pending(account_id: str, password: str) -> bool:
    """``password`` (the pending one) becomes current; ``False`` (logged) when it could not."""
    try:
        promoted = await promote_account_twofa_pending(account_id, password)
    except Exception:  # noqa: BLE001 - the RPC already succeeded; the pending copy stays
        promoted = False
    if not promoted:
        await log_event(
            "ERROR",
            "account_twofa_store_failed",
            account_id=account_id,
            extra={"has_hint": False, "clearing": False},
        )
    return promoted


class Settled(NamedTuple):
    """What a settlement left in the REAL columns.

    ``password`` is the stored current password, or, while ``unsettled``, the best guess
    (``current or pending``) for a caller that only AUTHORISES with it. ``unsettled``
    means a pending candidate is still in the row that is neither proven applied nor
    proven not applied: a caller that writes the columns must not stage over it.
    """

    password: str | None
    unsettled: bool


# (account_id) -> (digest of the pair last left unsettled, monotonic time). A digest, never
# the passwords: it only has to recognise "the same pair again". Cleared with the process.
_UNSETTLED_AT: dict[str, tuple[bytes, float]] = {}


def _pair_digest(current: str | None, pending: str) -> bytes:
    return hashlib.sha256(f"{len(current or '')}:{current or ''}{pending}".encode()).digest()


def _recently_unsettled(account_id: str, digest: bytes) -> bool:
    seen = _UNSETTLED_AT.get(account_id)
    interval = settings.telegram.settle_recheck_seconds
    return seen is not None and seen[0] == digest and time.monotonic() - seen[1] < interval


async def _settle_against_live_password(
    account_id: str,
    current: str | None,
    pending: str,
    verify: PasswordVerifier,
) -> None:
    """Telegram holds a password: keep whichever of ``current`` / ``pending`` it accepts.

    Writes only what a PROOF supports; anything unproven leaves both columns as they are.
    """
    if current is not None:
        accepted = await verify(current)
        if accepted is not False:
            if accepted:
                await stage_pending(account_id, None)
            return
    if await verify(pending):
        await promote_pending(account_id, pending)
    # A refusal is never a reason to drop ``pending``: with no ``current`` it is the only
    # copy of whatever Telegram holds, and a verdict can be wrong (a stale challenge).


async def resolve_current(
    account_id: str,
    status: TwoFactorStatusResult | None,
    verify: PasswordVerifier,
) -> Settled:
    """The password to authorise a write with, settling an unresolved pending candidate.

    No pending candidate: just the stored current one (no Telegram call). Otherwise the
    live state decides. Telegram holds no password: the candidate was never applied,
    drop it. Holds one: ask Telegram which of ``current`` / ``pending`` it accepts and
    keep that one as current. Anything unknown keeps both and reports ``unsettled``; the
    verdict is read back from the columns, never from what this function intended.

    The same unsettled pair is not put to Telegram again within
    ``settle_recheck_seconds``: every ask spends ``checkPassword`` attempts.
    Call it under :func:`services.accounts.twofa.twofa_lock`.
    """
    current, pending = await fetch_account_twofa_pair(account_id)
    if pending is None or pending == current:
        return Settled(password=current, unsettled=False)
    if status is None:
        return Settled(password=current or pending, unsettled=True)
    digest = _pair_digest(current, pending)
    if status.has_password and _recently_unsettled(account_id, digest):
        return Settled(password=current or pending, unsettled=True)
    if not status.has_password:
        await stage_pending(account_id, None)
    else:
        await _settle_against_live_password(account_id, current, pending, verify)
    current, pending = await fetch_account_twofa_pair(account_id)
    unsettled = pending is not None and pending != current
    if unsettled and pending is not None:
        _UNSETTLED_AT[account_id] = (_pair_digest(current, pending), time.monotonic())
    else:
        _UNSETTLED_AT.pop(account_id, None)
    return Settled(password=current or pending, unsettled=unsettled)
