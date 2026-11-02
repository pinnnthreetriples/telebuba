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
from core.repositories.session_audits import fetch_session_audit, save_session_audit
from core.telegram_client import SessionAuditGatewayError, read_account_sessions
from schemas.session_audit import AccountSessionAudit
from services.accounts._result import AccountActionError, AccountNotFoundError
from services.accounts.lifecycle import require_account
from services.lifecycle_journal import record_lifecycle_event

if TYPE_CHECKING:
    from schemas.accounts import AccountRead

__all__ = ["audit_account_sessions", "audit_after_first_check", "read_account_session_audit"]

# One lock per account, never freed (bounded by the accounts ever touched): a double click
# or a check racing the automatic audit must read, compare and save as one step.
_AUDIT_LOCKS: dict[str, asyncio.Lock] = {}


def _audit_lock(account_id: str) -> asyncio.Lock:
    return _AUDIT_LOCKS.setdefault(account_id, asyncio.Lock())


def _foreign_hashes(audit: AccountSessionAudit | None) -> set[str]:
    if audit is None:
        return set()
    return {session.hash for session in audit.sessions if not session.is_ours}


async def read_account_session_audit(account_id: str) -> AccountSessionAudit:
    """The account's latest saved audit; an empty view (no ``checked_at``) before the first."""
    await require_account(account_id)
    return await fetch_session_audit(account_id) or AccountSessionAudit.empty(account_id)


async def audit_account_sessions(account_id: str) -> AccountSessionAudit:
    """Read the account's sessions from Telegram, save them, journal a new foreign one.

    The journal row is written only for a foreign session the previous audit did not list,
    so re-checking an account that already has one does not repeat the news.
    """
    account = await require_account(account_id)
    if account.proxy_id is None:
        code = "session_audit_proxy_missing"
        raise AccountActionError(code)
    async with _audit_lock(account_id):
        try:
            listing = await read_account_sessions(account_id, account.devices)
        except SessionAuditGatewayError as exc:
            await log_event(
                "WARNING",
                "session_audit_failed",
                account_id=account_id,
                extra={"code": exc.code, "error_type": exc.error_type},
            )
            raise AccountActionError(exc.code, retry_after_seconds=exc.retry_after_seconds) from exc
        previous = await fetch_session_audit(account_id)
        audit = AccountSessionAudit.of(account_id, datetime.now(UTC).isoformat(), listing)
        await save_session_audit(audit)
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
