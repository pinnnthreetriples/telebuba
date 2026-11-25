"""The bound on wrong cloud-password answers: what every sender of a password consults.

Telegram answers a wrong password with ``PASSWORD_HASH_INVALID`` and, after enough of them, a
long flood wait; a stored password that went stale, retried by every resend of a code or every
new QR, looks like an attack on the account. Each account therefore has a budget of REJECTIONS
per window (``SESSION_AUDIT__WRONG_ANSWER_LIMIT`` per ``..._WINDOW_HOURS``), persisted with
the account so it survives a restart and a new sign-in attempt.

Once it is spent, a STORED password is no longer sent by itself (:func:`ensure_twofa_budget`).
A password the operator typed for this attempt still is: that is a human decision, and its
rejection is counted like any other (:func:`record_password_rejection`). A password that is
accepted, or replaced by the operator, starts a fresh budget (the repository does that).

No password ever reaches a log field here.
"""

from __future__ import annotations

from core.config import settings
from core.logging import log_event
from core.repositories._accounts_twofa import record_twofa_rejection, remaining_twofa_attempts

__all__ = [
    "TwofaAttemptsExhaustedError",
    "ensure_twofa_budget",
    "record_password_rejection",
]


class TwofaAttemptsExhaustedError(Exception):
    """A stored password was NOT sent: the account's wrong answers for this window are spent."""


async def ensure_twofa_budget(account_id: str) -> None:
    """Raise :class:`TwofaAttemptsExhaustedError` when a stored password must not be sent now."""
    audit = settings.session_audit
    remaining = await remaining_twofa_attempts(
        account_id,
        max_rejections=audit.wrong_answer_limit,
        window_hours=audit.wrong_answer_window_hours,
    )
    if remaining <= 0:
        raise TwofaAttemptsExhaustedError


async def record_password_rejection(account_id: str) -> None:
    """Count one ``PASSWORD_HASH_INVALID`` for ``account_id``; journal the moment it runs out.

    The journal row (``twofa_attempts_exhausted``) is written once per window, by the
    rejection that spends the last of the budget, not by every attempt that is then blocked.
    A failed write never hides the rejection from the caller.
    """
    audit = settings.session_audit
    try:
        count = await record_twofa_rejection(
            account_id, window_hours=audit.wrong_answer_window_hours
        )
    except Exception as exc:  # noqa: BLE001 - the answer itself still goes to the caller
        await log_event(
            "WARNING",
            "twofa_budget_write_failed",
            account_id=account_id,
            extra={"error_type": type(exc).__name__},
        )
        return
    if count == audit.wrong_answer_limit:
        await log_event(
            "WARNING",
            "twofa_attempts_exhausted",
            account_id=account_id,
            extra={"window_hours": audit.wrong_answer_window_hours, "count": count},
        )
