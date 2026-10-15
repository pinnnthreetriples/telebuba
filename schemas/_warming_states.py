"""The warming state vocabulary and its predicates.

Split from ``schemas.warming`` for the file-size budget; that module re-exports every
name here, so ``from schemas.warming import is_warming`` keeps working. Data contract
plus pure predicates over it — no I/O.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
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


def spam_block_known(record: WarmingStateRecord | None) -> bool:
    """True once @SpamBot confirmed this account life's spam block.

    From the stop itself (``spam_blocked``), and for good once the operator kept the
    account (``spam_blocked_at``): see ``services.warming._spam_block``.
    """
    if record is None:
        return False
    return record.state == "spam_blocked" or record.spam_blocked_at is not None
