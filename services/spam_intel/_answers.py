"""The shapes an ask is answered in, and the small pure helpers around them.

Split out of the gateway for its size: nothing here reads the clock (the caller passes the
instant) or touches the database, so a test patches the gateway's clock once and the answers
follow. The one piece of state is the log throttle, which keeps a polling caller from filling the
log with the same refusal.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.logging import log_event
from schemas.spam_status import SpamStatusVerdict
from services import _account_owner

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.spam_intel import AttemptOutcome, BudgetSnapshot, Purpose, Reason, ServedFrom
    from services.spam_intel._policy import Decision

# The statuses an account may be ASKED in (design 5.5 rule 1). ``new`` is an account no session
# check has judged yet, which nothing says is dead.
ASKABLE = frozenset({"new", "alive"})
# Faults the session check sets and clears by itself (a flood wait, a proxy or network error): the
# account is not asked NOW, but it is not refused either. A refusal would be counted by a caller
# (warming's quarantine) as an unreadable answer; a deferral with ``retry_at`` is not one.
TRANSIENT = frozenset({"flood_wait", "proxy_error", "network_error", "unknown_error"})


def link_failed(account: AccountRead) -> bool:
    """The account cannot be asked NOW: a transient status, or a failed link beside a key status.

    The session check keeps what it knows of the key in ``status`` and notes a flood wait, a
    proxy or a network fault in ``link_status``; either spelling is the same deferral.
    """
    return account.status in TRANSIENT or account.link_status is not None


# Every other status is a session that cannot answer (production showed five probes of a
# revoked one): refused.

# What each named caller is asking for. A caller not listed is a DECISION when it forces
# and ROUTINE otherwise.
CALLER_PURPOSE: dict[str, Purpose] = {
    "warming.cycle": "routine",
    "warming.quarantine": "decision",
    "warming.handoff": "decision",
    "survival.window": "measurement",
    "survival.block_recheck": "measurement",
    "neurocomment.onboarding": "routine",
    "neurocomment.ban_confirm": "decision",
    "neurocomment.ban_budget": "decision",
    "chat_broadcast.banned": "decision",
    "chat_broadcast.rest_end": "decision",
    "api.operator": "operator",
}

# (account, rule) -> when the last refusal line was written.
_LOGGED: dict[tuple[str, str], datetime] = {}
_LOG_THROTTLE = timedelta(minutes=10)


def forget_logged() -> None:
    """Drop the log throttle (tests only)."""
    _LOGGED.clear()


@dataclass(frozen=True)
class Ask:
    """One resolved ask: who, why, and what the caller is."""

    account_id: str
    force: bool
    purpose: Purpose
    caller: str
    holder: str | None
    confirmed: bool


@dataclass(frozen=True)
class Ended:
    """How a real probe ended, for the ledger and the account's state."""

    label: AttemptOutcome
    error: str | None
    at: datetime


def unasked(  # noqa: PLR0913 - keyword-only facts of one answer.
    account_id: str,
    *,
    served_from: ServedFrom,
    reason: Reason,
    at: datetime,
    retry_at: datetime | None = None,
    detail: str | None = None,
) -> SpamStatusVerdict:
    """An ``unknown`` the budget produced instead of a reading, saying why and until when."""
    return SpamStatusVerdict(
        account_id=account_id,
        status="unknown",
        detail=detail or f"{served_from}:{reason}",
        checked_at=at.isoformat(),
        served_from=served_from,
        retry_at=None if retry_at is None else retry_at.isoformat(),
        reason=reason,
    )


def coalesced(
    account_id: str,
    cached: SpamStatusVerdict | None,
    snapshot: BudgetSnapshot,
    at: datetime,
) -> SpamStatusVerdict:
    """The last reading (or failure) handed to a caller who asks again inside the window.

    The stored reading is "the last reading" only when the attempt that opened the window
    produced it. An attempt that failed, was unreadable, or died (crash, cancellation, a
    restart: its ledger row never closed with a reading) leaves what was stored before it
    uncertain, so the answer is an ``unknown`` that carries the failure and not that reading.
    """
    state = snapshot.state
    failed = (state is not None and state.last_outcome in {"error", "unknown"}) or (
        snapshot.last_attempt_outcome in {"started", "error", "unknown"}
    )
    if cached is not None and not failed:
        return cached.model_copy(update={"served_from": "coalesced"})
    detail = snapshot.last_attempt_error or (state.last_error if state is not None else None)
    return SpamStatusVerdict(
        account_id=account_id,
        status="unknown",
        detail=detail,
        checked_at=at.isoformat(),
        served_from="coalesced",
    )


def outcome_label(verdict: SpamStatusVerdict, error: str | None) -> AttemptOutcome:
    """The ledger word for a finished probe: its status, or ``error`` if it never got one."""
    return "error" if error is not None else verdict.status


def busy(account_id: str, holder: str | None) -> bool:
    """Whether another feature holds (or has borrowed) the account the caller is not."""
    owner = _account_owner.owner_of(account_id)
    return (owner is not None and owner != holder) or _account_owner.is_lent(account_id)


async def log_unasked(account_id: str, decision: Decision, req: Ask, at: datetime) -> None:
    """Write one line for a coalesced, postponed or refused ask, at most once per window."""
    key = (account_id, decision.reason or decision.action)
    last = _LOGGED.get(key)
    if last is not None and at - last < _LOG_THROTTLE:
        return
    _LOGGED[key] = at
    if decision.action == "coalesce":
        await log_event(
            "INFO",
            "spam_intel_coalesced",
            account_id=account_id,
            extra={"caller": req.caller, "purpose": req.purpose},
        )
        return
    retry = None if decision.retry_at is None else decision.retry_at.isoformat()
    await log_event(
        "INFO",
        "spam_intel_probe_refused",
        account_id=account_id,
        extra={
            "caller": req.caller,
            "purpose": req.purpose,
            "action": decision.action,
            "reason": decision.reason,
            "retry_at": retry,
        },
    )
    if decision.reason == "fleet_hard_cap":
        await log_event(
            "WARNING",
            "spam_intel_budget_exceeded",
            extra={"cap": "hard", "limit": settings.spam_intel.fleet_hard_cap},
        )
