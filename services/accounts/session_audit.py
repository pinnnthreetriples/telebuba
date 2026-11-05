"""Session audit for the accounts domain — who else holds a session of the account.

Why this exists: an account bought from a seller keeps working only while nobody else holds
a session of it, and whoever does can end ours (``terminate all other sessions``) or has
kept a copy of the auth key. Telebuba never logs other sessions out; this reads what
Telegram lists, marks the account's own sessions (``core.telegram_client``'s gateway asks
each of our clients which one it is), saves the answer and journals the moment a session
that is not ours first shows up. A later death can then be bracketed by "the last audit saw
N sessions at T".

Read-only towards Telegram. The audit talks to Telegram, so an account with no proxy is
refused first: the import itself is deliberately offline until a proxy is assigned, and a
proxyless client connects from the operator's own IP.

The gateway function is imported at module scope so tests monkeypatch it at
``services.accounts.session_audit.<name>`` (the convention :mod:`.login` documents).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.logging import log_event
from core.repositories.account_own_authorizations import (
    fetch_own_authorizations,
    prune_own_authorizations,
)
from core.repositories.account_rekeys import fetch_rekey
from core.repositories.session_audits import fetch_session_audit, save_session_audit
from core.telegram_client import SessionAuditGatewayError, read_account_sessions
from schemas.session_audit import AccountSessionAudit
from services.accounts._result import AccountActionError, AccountNotFoundError
from services.accounts._session_classify import flag_seller_keys, with_own
from services.accounts.lifecycle import require_account
from services.lifecycle_journal import record_lifecycle_event

if TYPE_CHECKING:
    from schemas.accounts import AccountRead

__all__ = [
    "account_audit_lock",
    "audit_account_sessions",
    "audit_after_first_check",
    "read_account_session_audit",
    "refresh_account_audit",
    "require_proxied_account",
]

# One lock per account, never freed (bounded by the accounts ever touched): a double click,
# a check racing the automatic audit, or a close must read, compare and save as one step.
_AUDIT_LOCKS: dict[str, asyncio.Lock] = {}


def account_audit_lock(account_id: str) -> asyncio.Lock:
    return _AUDIT_LOCKS.setdefault(account_id, asyncio.Lock())


def _foreign_hashes(audit: AccountSessionAudit | None) -> set[str]:
    if audit is None:
        return set()
    return {session.hash for session in audit.sessions if not session.is_ours}


async def read_account_session_audit(account_id: str) -> AccountSessionAudit:
    """The account's latest saved audit; an empty view (no ``checked_at``) before the first."""
    await require_account(account_id)
    saved = await fetch_session_audit(account_id)
    if saved is None:
        return AccountSessionAudit.empty(account_id)
    # An own web login recorded after the audit was read is ours now, not foreign.
    return with_own(saved, await fetch_own_authorizations(account_id))


async def require_proxied_account(account_id: str) -> AccountRead:
    """The account row, refused (``session_audit_proxy_missing``) before any Telegram call.

    Every audit and every close talks to Telegram, and a proxyless client connects from the
    operator's own IP.
    """
    account = await require_account(account_id)
    if account.proxy_id is None:
        code = "session_audit_proxy_missing"
        raise AccountActionError(code)
    return account


async def refresh_account_audit(
    account: AccountRead, *, killed_count: int = 0
) -> AccountSessionAudit:
    """Read the account's sessions, save them and journal a new foreign one.

    The caller holds :func:`account_audit_lock`. The journal row is written only for a
    foreign session the previous audit did not list, so re-checking an account that
    already has one does not repeat the news. ``killed_count`` is how many sessions a close
    ended just before this read, kept in the audit history.
    """
    account_id = account.account_id
    try:
        listing = await read_account_sessions(
            account_id, account.devices, await fetch_own_authorizations(account_id)
        )
    except SessionAuditGatewayError as exc:
        await log_event(
            "WARNING",
            "session_audit_failed",
            account_id=account_id,
            extra={"code": exc.code, "error_type": exc.error_type},
        )
        raise AccountActionError(exc.code, retry_after_seconds=exc.retry_after_seconds) from exc
    if listing.sessions:
        # A recorded row whose authorization is gone (ended, or expired) must not linger: a
        # hash is never reused, but the table would only ever grow.
        await prune_own_authorizations(account_id, [s.hash for s in listing.sessions])
    previous = await fetch_session_audit(account_id)
    listing = listing.model_copy(
        update={
            "sessions": flag_seller_keys(
                listing.sessions, account.created_at, await fetch_rekey(account_id)
            )
        }
    )
    audit = AccountSessionAudit.of(account_id, datetime.now(UTC).isoformat(), listing)
    await save_session_audit(audit, killed_count=killed_count)
    await log_event(
        "INFO",
        "session_audit_completed",
        account_id=account_id,
        extra={"total": audit.total, "foreign": audit.foreign_count},
    )
    new_foreign = _foreign_hashes(audit) - _foreign_hashes(previous)
    if new_foreign:
        extra: dict[str, object] = {
            "total": audit.total,
            "foreign": audit.foreign_count,
            "new": len(new_foreign),
        }
        await log_event(
            "WARNING", "session_audit_foreign_found", account_id=account_id, extra=extra
        )
        await record_lifecycle_event(account_id, "foreign_sessions_found", extra)
    return audit


async def audit_account_sessions(account_id: str) -> AccountSessionAudit:
    """Read the account's sessions from Telegram now, save them, journal a new foreign one."""
    account = await require_proxied_account(account_id)
    async with account_audit_lock(account_id):
        return await refresh_account_audit(account)


async def audit_after_first_check(account: AccountRead) -> None:
    """Audit an account once, right after the first check that found it alive behind a proxy.

    A no-op for an account without a proxy (the import flow is offline until one is
    assigned) or one already audited. A refusal does not propagate: this rides on a session
    check, which must answer whatever the audit finds, and the audit already logged it.
    """
    if account.proxy_id is None or await fetch_session_audit(account.account_id) is not None:
        return
    try:
        await audit_account_sessions(account.account_id)
    except (AccountActionError, AccountNotFoundError):
        return
