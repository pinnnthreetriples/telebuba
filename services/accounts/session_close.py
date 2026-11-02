"""Closing the sessions of an account that are not ours — an explicit operator action.

Why this exists: a session somebody else holds (the seller, the previous owner) can end
ours at any moment. The audit (:mod:`.session_audit`) shows them; this ends them, one
``account.resetAuthorization`` per session, never "terminate all others" (an account can
hold a second sign-in of its own). Two entries share one run:

* :func:`close_account_sessions` — the hashes the operator saw in the confirm dialog. The
  gateway reads the list again first and refuses what is no longer foreign.
* :func:`close_account_foreign_sessions` — whatever is foreign right now, for the bulk run
  and the import's "close them automatically" option.

Telegram refuses the call from a session younger than 24 hours; that answer is an outcome
(``too_new`` with the time left), not an error. The pauses between sessions are drawn here
(core holds no randomness). Afterwards the account is audited again so the result on screen
is what Telegram lists, and the close is journalled (counts only) and kept in the audit
history.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from core.config import settings
from core.logging import log_event
from core.repositories.session_audits import fetch_session_audit
from core.telegram_client import SessionAuditGatewayError, close_sessions
from schemas.session_close import SessionCloseOutcome, SessionCloseResult
from services import pacing
from services.accounts._result import AccountActionError
from services.accounts.session_audit import (
    account_audit_lock,
    refresh_account_audit,
    require_proxied_account,
)
from services.lifecycle_journal import record_lifecycle_event

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.session_audit import AccountSessionAudit

__all__ = ["close_account_foreign_sessions", "close_account_sessions"]

# Seam for tests; the pauses are the only thing drawn.
_rng = random.Random()  # nosec B311  # noqa: S311 - pacing jitter, not security

# An outcome that is not a refusal: the session is gone, as the operator wanted.
_DONE = frozenset({"closed", "gone"})


def _pauses(count: int) -> list[float]:
    """One human-like pause for every gap between ``count`` sessions."""
    limits = settings.session_audit
    return [
        pacing.human_delay(
            limits.close_pause_min_seconds,
            limits.close_pause_max_seconds,
            rng=_rng,
            mu=limits.delay_lognorm_mu,
            sigma=limits.delay_lognorm_sigma,
        )
        for _ in range(max(0, count - 1))
    ]


async def _close(account_id: str, hashes: list[str] | None) -> SessionCloseResult:
    account = await require_proxied_account(account_id)
    async with account_audit_lock(account_id):
        if hashes is None:
            # Read and saved first, so the foreign set is what Telegram lists now.
            before = await refresh_account_audit(account)
            hashes = [session.hash for session in before.sessions if not session.is_ours]
        outcomes: list[SessionCloseOutcome] = []
        if hashes:
            try:
                outcomes = await close_sessions(
                    account_id, account.devices, hashes, _pauses(len(hashes))
                )
            except SessionAuditGatewayError as exc:
                await log_event(
                    "WARNING",
                    "session_close_failed",
                    account_id=account_id,
                    extra={"code": exc.code, "error_type": exc.error_type},
                )
                raise AccountActionError(
                    exc.code, retry_after_seconds=exc.retry_after_seconds
                ) from exc
        closed = sum(1 for outcome in outcomes if outcome.status == "closed")
        audit = await _reread(account, outcomes, closed)
    await _report(account_id, hashes, outcomes, closed)
    return SessionCloseResult(
        account_id=account_id, outcomes=outcomes, closed_count=closed, audit=audit
    )


async def _reread(
    account: AccountRead, outcomes: list[SessionCloseOutcome], closed: int
) -> AccountSessionAudit | None:
    """The account's sessions after the run; the saved ones when nothing was tried."""
    if not outcomes:
        return await fetch_session_audit(account.account_id)
    try:
        return await refresh_account_audit(account, killed_count=closed)
    except AccountActionError:
        # The closes already happened and are reported; the failed read logged itself.
        return None


async def _report(
    account_id: str, hashes: list[str], outcomes: list[SessionCloseOutcome], closed: int
) -> None:
    refused = sum(1 for outcome in outcomes if outcome.status not in _DONE)
    extra: dict[str, object] = {"requested": len(hashes), "closed": closed, "refused": refused}
    await log_event(
        "WARNING" if refused else "INFO",
        "session_close_completed",
        account_id=account_id,
        extra=extra,
    )
    if closed:
        await record_lifecycle_event(
            account_id, "foreign_sessions_closed", {"requested": len(hashes), "closed": closed}
        )


async def close_account_sessions(account_id: str, hashes: list[str]) -> SessionCloseResult:
    """Close the named sessions the gateway still finds foreign; see the module doc."""
    return await _close(account_id, list(dict.fromkeys(hashes)))


async def close_account_foreign_sessions(account_id: str) -> SessionCloseResult:
    """Close every session that is foreign right now; see the module doc."""
    return await _close(account_id, None)
