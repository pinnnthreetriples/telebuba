"""Admission and reservation as ONE step (design 5.5), and who is refused before any budget.

The budget is computed from the ledger, so "may this account be asked" and "write the row that
says it was" must not be separated: two asks that read one snapshot would both be admitted and
the proxy spacing, the fleet gap and the hard cap would be crossed by exactly the burst they
exist to stop. :func:`gate` is the one in-process lock around "load the snapshot, decide, write
the attempt row" for every real question, a probe or a history read alike. One uvicorn worker
makes an in-process lock sound; the lock is never held across a network call or a wait: only
the decision and the one insert.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.repositories.spam_probe_ledger import start_attempt
from schemas.spam_intel import ProbeAttemptStart
from services.spam_intel._answers import ASKABLE, log_unasked, unasked
from services.spam_intel._policy import Decision

if TYPE_CHECKING:
    from datetime import datetime

    from schemas.accounts import AccountRead
    from schemas.spam_intel import BudgetSnapshot, Reason
    from schemas.spam_status import SpamStatusVerdict
    from services.spam_intel._answers import Ask


class _Gate:
    """The lock, built lazily for the running loop (a lock is bound to one loop)."""

    lock: asyncio.Lock | None = None
    loop: asyncio.AbstractEventLoop | None = None


_gate = _Gate()


def gate() -> asyncio.Lock:
    """The admission lock of the running event loop."""
    loop = asyncio.get_running_loop()
    if _gate.lock is None or _gate.loop is not loop:
        _gate.lock, _gate.loop = asyncio.Lock(), loop
    return _gate.lock


def reset() -> None:
    """Forget the lock (tests only: each test runs its own loop)."""
    _gate.lock = _gate.loop = None


@dataclass(frozen=True)
class Admission:
    """The budget's word on one ask, the snapshot it was made from and, on ``probe``, the row."""

    decision: Decision
    snapshot: BudgetSnapshot
    at: datetime
    # The reserved ledger row; ``None`` unless the decision is ``probe``.
    attempt_id: int | None = None


def refusal_reason(account: AccountRead) -> Reason | None:
    """Why the account is never asked (rules 1 and 2), or ``None`` when it may be."""
    if account.status not in ASKABLE:
        return "account_not_alive"
    return "no_proxy" if account.proxy_id is None else None


async def refuse(
    req: Ask, reason: Reason, at: datetime, *, detail: str | None = None
) -> SpamStatusVerdict:
    """The refused answer for a guard that fired before any budget was read, and its event."""
    await log_unasked(req.account_id, Decision(action="refuse", reason=reason), req, at)
    return unasked(req.account_id, served_from="refused", reason=reason, at=at, detail=detail)


async def reserve(account: AccountRead, req: Ask, at: datetime) -> int:
    """Write the attempt row of a probe the budget just admitted (call inside :func:`gate`)."""
    return await start_attempt(
        ProbeAttemptStart(
            account_id=account.account_id,
            proxy_key=account.proxy_id,
            purpose=req.purpose,
            caller=req.caller,
            started_at=at.isoformat(),
        )
    )
