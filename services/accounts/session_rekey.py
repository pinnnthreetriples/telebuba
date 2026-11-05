"""Re-keying an account's session — an explicit operator action, never automatic.

Why this exists: an imported seller ``.session`` shares its auth key with whoever kept a
copy of it, and that person can use or end it without ever appearing as another device.
The re-key signs the account in again as a session of its own (``core.telegram_client``'s
``rekey_session``: a login token the imported session approves), swaps the session file,
and then ends the OLD authorization, the key the seller knows.

Ending the old authorization needs a session Telegram trusts to end others, and a session
younger than 24 hours is refused (``FRESH_RESET_AUTHORISATION_FORBIDDEN``). Whether one made
by a login token counts as young is not documented, so the close is TRIED right away and a
refusal is not a failure: the old hash is stored with the time it may be tried again, and a
background pass (:func:`close_due_old_keys`, run by ``services.session_guard``) finishes the
job. Until then the card says when it closes.

One device at a time: the account's primary session. A second sign-in of its own (another
device type) is its own authorization with its own key and is left alone.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.db import fetch_account
from core.logging import log_event
from core.repositories._accounts_twofa import fetch_account_twofa_password
from core.repositories.account_rekeys import fetch_rekey, list_due_rekeys, save_rekey
from core.telegram_client import (
    RekeyGatewayError,
    SessionAuditGatewayError,
    close_sessions,
    rekey_session,
)
from schemas.session_rekey import AccountRekeyView, StoredRekey
from services._account_owner import is_lent, owner_of
from services.accounts._result import AccountActionError
from services.accounts.lifecycle import require_account
from services.accounts.session_audit import account_audit_lock, refresh_account_audit

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.session_close import SessionCloseOutcome

__all__ = ["close_due_old_keys", "read_account_rekey", "rekey_account_session"]

# Added to a wait Telegram named, so the retry lands after it rather than on its edge.
_MARGIN = timedelta(seconds=60)


def _now() -> datetime:
    return datetime.now(UTC)


def _view(account_id: str, rekey: StoredRekey | None) -> AccountRekeyView:
    if rekey is None:
        return AccountRekeyView(account_id=account_id)
    return AccountRekeyView(
        account_id=account_id,
        rekeyed_at=rekey.rekeyed_at,
        closed_at=rekey.closed_at,
        close_after=rekey.close_after,
    )


async def read_account_rekey(account_id: str) -> AccountRekeyView:
    """The account's last re-key and whether its old authorization is ended yet."""
    await require_account(account_id)
    return _view(account_id, await fetch_rekey(account_id))


def _next_try(outcome: SessionCloseOutcome | None) -> datetime:
    """When the old authorization may be tried again, given how the last try ended."""
    wait = outcome.retry_after_seconds if outcome is not None else None
    if wait:
        return _now() + timedelta(seconds=wait) + _MARGIN
    return _now() + timedelta(seconds=settings.session_audit.rekey_retry_seconds)


async def _end_old_key(account: AccountRead, rekey: StoredRekey) -> StoredRekey:
    """Try to end the old authorization; the row says it is ended, or when to try again.

    The caller holds the account's audit lock. Ended (or already gone) is final; anything
    else, a refusal for being too young included, schedules the next try. The close goes
    through the same gateway as the foreign-session close, which never names one of ours.
    """
    outcome: SessionCloseOutcome | None = None
    try:
        outcomes = await close_sessions(account.account_id, account.devices, [rekey.old_hash], [])
        outcome = outcomes[0] if outcomes else None
    except SessionAuditGatewayError as exc:
        await log_event(
            "WARNING",
            "session_old_key_pending",
            account_id=account.account_id,
            extra={"code": exc.code},
        )
    if outcome is not None and outcome.status in {"closed", "gone"}:
        ended = rekey.model_copy(update={"close_after": None, "closed_at": _now().isoformat()})
        await save_rekey(ended)
        await log_event("INFO", "session_old_key_closed", account_id=account.account_id)
        return ended
    pending = rekey.model_copy(update={"close_after": _next_try(outcome).isoformat()})
    await save_rekey(pending)
    await log_event(
        "INFO",
        "session_old_key_pending",
        account_id=account.account_id,
        extra={"status": outcome.status if outcome is not None else "unreachable"},
    )
    return pending


async def _audit_again(account: AccountRead) -> None:
    """Read the sessions anew so the card shows the new key; a failed read logged itself."""
    try:
        await refresh_account_audit(account)
    except AccountActionError:
        return


async def rekey_account_session(account_id: str) -> AccountRekeyView:
    """Give the account a login of its own, then end the old authorization (or schedule it)."""
    account = await require_account(account_id)
    if account.proxy_id is None:
        code = "rekey_proxy_missing"
        raise AccountActionError(code)
    if owner_of(account_id) is not None or is_lent(account_id):
        code = "rekey_account_busy"
        raise AccountActionError(code)
    device = account.devices[0]
    scratch = f"{account.session_name or account_id}_rekey"
    password = await fetch_account_twofa_password(account_id)
    async with account_audit_lock(account_id):
        # Inside the lock: a concurrent rekey that finished while this one waited has
        # left its pending row, and a second rekey must not start over it.
        previous = await fetch_rekey(account_id)
        if previous is not None and previous.closed_at is None:
            code = "rekey_pending"
            raise AccountActionError(code)
        try:
            result = await rekey_session(account_id, device, scratch, password)
        except RekeyGatewayError as exc:
            await log_event(
                "WARNING",
                "session_rekey_failed",
                account_id=account_id,
                extra={"code": exc.code, "error_type": exc.error_type},
            )
            raise AccountActionError(exc.code, retry_after_seconds=exc.retry_after_seconds) from exc
        stored = StoredRekey(
            account_id=account_id,
            old_hash=result.old_hash,
            rekeyed_at=_now().isoformat(),
            close_after=_now().isoformat(),
        )
        await save_rekey(stored)
        await log_event("INFO", "session_rekeyed", account_id=account_id)
        stored = await _end_old_key(account, stored)
        await _audit_again(account)
    return _view(account_id, stored)


async def close_due_old_keys() -> int:
    """Try every old authorization whose time has come; how many were ended.

    The background pass (``services.session_guard``). One account at a time, each under its
    audit lock; an account that was deleted since is skipped (its row went with it).
    """
    ended = 0
    for rekey in await list_due_rekeys(_now().isoformat()):
        account = await fetch_account(rekey.account_id)
        if account is None:
            continue
        async with account_audit_lock(rekey.account_id):
            current = await fetch_rekey(rekey.account_id)
            if current is None or current.closed_at is not None:
                continue
            after = await _end_old_key(account, current)
            if after.closed_at is not None:
                ended += 1
                await _audit_again(account)
    return ended
