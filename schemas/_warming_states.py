"""The warming state vocabulary and its predicates.

Split from ``schemas.warming`` for the file-size budget; that module re-exports every
name here, so ``from schemas.warming import is_warming`` keeps working. Data contract
plus pure predicates over it — no I/O.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from schemas.spam_status import SpamStatusVerdict
    from schemas.warming import WarmingStateRecord

WarmingState = Literal[
    "idle", "active", "sleeping", "flood_wait", "quarantine", "error", "spam_blocked"
]
WarmingHealth = Literal["idle", "ok", "warn", "fail"]

_ACTIVE_STATES: frozenset[WarmingState] = frozenset(
    {"active", "sleeping", "flood_wait", "quarantine", "error", "spam_blocked"},
)
# On the warming board, yet no loop runs: the operator has to act first.
_PARKED_STATES: frozenset[WarmingState] = frozenset({"error", "spam_blocked"})


def warming_health(state: WarmingState) -> WarmingHealth:
    """Map a warming state to a traffic-light colour for the UI.

    - ``ok`` (green)  — actively warming.
    - ``warn`` (amber) — sleeping between cycles, flood-waited, or quarantined.
    - ``fail`` (red)  — last cycle errored, or @SpamBot confirmed a spam block.
    - ``idle`` (grey) — not being warmed.
    """
    if state == "active":
        return "ok"
    if state in {"sleeping", "flood_wait", "quarantine"}:
        return "warn"
    if state in _PARKED_STATES:
        return "fail"
    return "idle"


def is_warming(state: WarmingState) -> bool:
    """True when the account belongs in the right-hand (warming) kanban column."""
    return state in _ACTIVE_STATES


def is_parked(state: WarmingState) -> bool:
    """True for a warming-column state no loop runs in (``error``, ``spam_blocked``)."""
    return state in _PARKED_STATES


def _moment(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def lifts_spam_block(verdict: SpamStatusVerdict | None, blocked_at: str | None) -> bool:
    """True for a ``clean`` @SpamBot answer read after the block was confirmed."""
    if verdict is None or blocked_at is None or verdict.status != "clean":
        return False
    answered, blocked = _moment(verdict.checked_at), _moment(blocked_at)
    return answered is not None and blocked is not None and answered > blocked


def spam_block_known(
    record: WarmingStateRecord | None,
    spam: SpamStatusVerdict | None = None,
) -> bool:
    """True while @SpamBot's confirmed spam block on this account stands.

    From the stop itself (``spam_blocked``), and on after the operator kept the account
    (``spam_blocked_at``), until a ``clean`` answer lifts it
    (``services.spam_block_lift`` clears the mark). ``spam``: a reading the caller holds
    that may be newer than ``record`` — a ``clean`` one read after the mark already
    lifts the block, before the row says so.
    """
    if record is None or lifts_spam_block(spam, record.spam_blocked_at):
        return False
    return record.state == "spam_blocked" or record.spam_blocked_at is not None
