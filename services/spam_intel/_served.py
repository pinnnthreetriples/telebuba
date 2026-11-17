"""Evidence in the gateway's ask (design 5.2, 5.4, 5.5 rule 5), only with ``passive_decisions``.

Off, none of this runs: :func:`evidence_hint` returns an empty hint without touching the
database, and an ask goes exactly the Stage 1 way (cache, coalescing, budget, probe).

On, for a ROUTINE or DECISION ask (never MEASUREMENT, whose product is a real answer, nor the
operator's button, which shows what is known and asks only when told to):

* ``needs_probe`` (a contradiction, an expired dated limit, an undated limit past its recheck)
  means the stored reading cannot be trusted as fresh: the cache is skipped and the budget
  decides whether to ask. This is the only evidence-driven reason to ask.
* A FORCED ask is never answered from evidence: the callers that force are the irreversible steps
  (a sticky ban, a broadcast halt, a quarantine release) and they need a real answer. Forcing
  stays what Stage 1 made it: skip the cache TTL, still subject to the budget.
* Otherwise a high-confidence ``clean`` or ``limited`` fold answers without asking
  (``served_from="evidence"``); and a ROUTINE ask of an account with a long clean record is
  answered from its stored reading until the stretched interval (72 -> 120 -> 168 h) is over.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.logging import log_event
from schemas.spam_status import SpamStatusVerdict
from services.spam_intel._adaptive import last_clean_at, routine_interval
from services.spam_intel._evidence import current_view

if TYPE_CHECKING:
    from schemas.spam_evidence import Verdict
    from schemas.spam_status import SpamStatusKind
    from services.spam_intel._answers import Ask

# The purposes evidence may answer or advise. Measurement and operator are real asks.
_PURPOSES = frozenset({"routine", "decision"})
_LOG_EVERY = timedelta(hours=6)
# (account, how) -> when the last "served from evidence" line was written: a warming cycle asks
# again every cycle, and a stretched account would otherwise log each one.
_LOGGED: dict[tuple[str, str], datetime] = {}


@dataclass(frozen=True)
class EvidenceHint:
    """What the evidence says about this ask: an answer, or that the cache must not be trusted."""

    answer: SpamStatusVerdict | None = None
    recheck: bool = False


def forget_served() -> None:
    """Drop the log throttle (tests only)."""
    _LOGGED.clear()


async def _note(req: Ask, answer: SpamStatusVerdict, how: str, at: datetime) -> None:
    key = (req.account_id, how)
    last = _LOGGED.get(key)
    if last is not None and at - last < _LOG_EVERY:
        return
    _LOGGED[key] = at
    await log_event(
        "INFO",
        "spam_intel_served_from_evidence",
        account_id=req.account_id,
        extra={
            "caller": req.caller,
            "purpose": req.purpose,
            "status": answer.status,
            "via": how,
        },
    )


def _answerable(verdict: Verdict) -> SpamStatusKind | None:
    """``clean`` or ``limited`` when the fold is confident enough to answer without asking."""
    if verdict.confidence != "high":
        return None
    if verdict.status == "clean":
        return "clean"
    return "limited" if verdict.status == "limited" else None


def _stretched(
    last_clean: datetime | None,
    interval: timedelta | None,
    cached: SpamStatusVerdict | None,
    at: datetime,
) -> SpamStatusVerdict | None:
    """The stored ``clean`` reading, while the account's stretched interval is not over."""
    if interval is None or last_clean is None or cached is None or cached.status != "clean":
        return None
    if at - last_clean >= interval:
        return None
    return cached.model_copy(update={"served_from": "cache"})


async def evidence_hint(
    req: Ask,
    cached: SpamStatusVerdict | None,
    at: datetime,
) -> EvidenceHint:
    """The evidence's word on one ask; empty unless ``passive_decisions`` is on."""
    intel = settings.spam_intel
    if not (intel.enabled and intel.passive_decisions) or req.purpose not in _PURPOSES:
        return EvidenceHint()
    verdict, items = await current_view(req.account_id)
    if verdict.needs_probe:
        return EvidenceHint(recheck=True)
    if req.force:
        return EvidenceHint()
    if req.purpose == "routine" and verdict.status == "clean":
        stretched = _stretched(last_clean_at(items), routine_interval(items, intel), cached, at)
        if stretched is not None:
            await _note(req, stretched, "interval", at)
            return EvidenceHint(answer=stretched)
    status = _answerable(verdict)
    if status is not None:
        answer = (
            cached.model_copy(update={"served_from": "evidence"})
            if cached is not None and cached.status == status
            else SpamStatusVerdict(
                account_id=req.account_id,
                status=status,
                detail=verdict.primary_kind,
                checked_at=at.isoformat(),
                served_from="evidence",
            )
        )
        await _note(req, answer, "fold", at)
        return EvidenceHint(answer=answer)
    return EvidenceHint()
