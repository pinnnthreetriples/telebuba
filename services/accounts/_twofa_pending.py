"""The pending cloud-password candidate: staging, promotion and resolution.

A 2FA write stages its new password in ``accounts.twofa_password_pending`` BEFORE
``account.updatePasswordSettings`` is sent, so a crash or a lost reply after Telegram
applied it never leaves only the old password (the account would be lost). Sibling of
``services.accounts.twofa`` (file budget); the dependency runs one way, this module
never imports it, so the Telegram verify call is injected.

No value here may reach a log extra or an error message.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

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


async def promote_pending(account_id: str) -> bool:
    """Pending becomes current; ``False`` (logged) when it could not be stored."""
    try:
        promoted = await promote_account_twofa_pending(account_id)
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


async def _settle_against_live_password(
    account_id: str,
    current: str | None,
    pending: str,
    verify: PasswordVerifier,
) -> str | None:
    """Telegram holds a password: keep whichever of ``current`` / ``pending`` it accepts."""
    if current is not None:
        accepted = await verify(current)
        if accepted is not False:
            if accepted:
                await stage_pending(account_id, None)
            return current
    accepted = await verify(pending)
    if accepted:
        await promote_pending(account_id)
        return pending
    if accepted is False and current is None:
        await stage_pending(account_id, None)
        return None
    return current or pending


async def resolve_current(
    account_id: str,
    status: TwoFactorStatusResult | None,
    verify: PasswordVerifier,
) -> str | None:
    """The password to authorise a write with, settling an unresolved pending candidate.

    No pending candidate: just the stored current one (no Telegram call). Otherwise the
    live state decides. Telegram holds no password: the candidate was never applied,
    drop it. Holds one: ask Telegram which of ``current`` / ``pending`` it accepts and
    keep that one as current. Anything unknown keeps both, and the best guess is returned.
    """
    current, pending = await fetch_account_twofa_pair(account_id)
    if pending is None or pending == current:
        return current
    if status is None:
        return current or pending
    if not status.has_password:
        await stage_pending(account_id, None)
        return current
    return await _settle_against_live_password(account_id, current, pending, verify)
