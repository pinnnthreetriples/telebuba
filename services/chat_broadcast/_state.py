"""In-memory run state: generations, start/edit claims, the LLM budget.

The shape of ``services.neuroshilling._state``, for the same reasons: one process, one
event loop, so a function with no ``await`` in it is atomic, and nothing here is worth
persisting — a restart is a full repair from the campaign rows.

* A run GENERATION only ever rises; Stop bumps it and every external call checks it.
* ``_RUN_OWNER`` is the run entitled to write the terminal row, so a late finisher
  cannot settle ``done`` over its successor's ``running``.
* Start and settings saves exclude each other through counted claims.
"""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta

from core.config import settings

_RUN_GENERATIONS: dict[str, int] = {}
_RUN_OWNER: dict[str, str] = {}
_STARTING: set[str] = set()
_EDITING: dict[str, int] = {}
_LLM_CALLS: deque[datetime] = deque()
_LLM_WINDOW = timedelta(days=1)
_FALLBACKS: set[tuple[str, str]] = set()


def begin_run(campaign_id: str, run_id: str) -> int:
    generation = _RUN_GENERATIONS[campaign_id] = _RUN_GENERATIONS.get(campaign_id, 0) + 1
    _RUN_OWNER[campaign_id] = run_id
    return generation


def run_is_current(campaign_id: str, generation: int) -> bool:
    return _RUN_GENERATIONS.get(campaign_id) == generation


def abandon_run(campaign_id: str, run_id: str) -> None:
    """Forget a run that never reached the database or spawned a task."""
    if _RUN_OWNER.get(campaign_id) == run_id:
        _RUN_OWNER.pop(campaign_id, None)
        revoke_run(campaign_id)


def revoke_run(campaign_id: str) -> None:
    _RUN_GENERATIONS[campaign_id] = _RUN_GENERATIONS.get(campaign_id, 0) + 1


def revoke_run_if_current(campaign_id: str, run_id: str | None) -> bool:
    """Fence the run ``run_id`` names; ``False`` when a newer run owns the campaign."""
    owner = _RUN_OWNER.get(campaign_id)
    if run_id is not None and owner is not None and owner != run_id:
        return False
    revoke_run(campaign_id)
    return True


def claim_settlement(campaign_id: str, run_id: str) -> bool:
    """May ``run_id`` write the terminal row? Refused only to a superseded run."""
    owner = _RUN_OWNER.get(campaign_id)
    if owner is not None and owner != run_id:
        return False
    _RUN_OWNER.pop(campaign_id, None)
    return True


def try_claim_start(campaign_id: str) -> bool:
    if campaign_id in _STARTING or campaign_id in _EDITING:
        return False
    _STARTING.add(campaign_id)
    return True


def finish_start(campaign_id: str) -> None:
    _STARTING.discard(campaign_id)


def start_in_flight(campaign_id: str) -> bool:
    return campaign_id in _STARTING


def try_claim_edit(campaign_id: str) -> bool:
    if campaign_id in _STARTING:
        return False
    _EDITING[campaign_id] = _EDITING.get(campaign_id, 0) + 1
    return True


def finish_edit(campaign_id: str) -> None:
    remaining = _EDITING[campaign_id] - 1
    if remaining:
        _EDITING[campaign_id] = remaining
    else:
        del _EDITING[campaign_id]


def at_daily_llm_cap(now: datetime | None = None) -> bool:
    """Has the fleet spent its rolling-24h rewrite budget? (0 = no rewrites at all)."""
    moment = now or datetime.now(UTC)
    while _LLM_CALLS and _LLM_CALLS[0] < moment - _LLM_WINDOW:
        _LLM_CALLS.popleft()
    return len(_LLM_CALLS) >= settings.chat_broadcast.max_llm_calls_per_day


def record_llm_call(*, calls: int = 1, now: datetime | None = None) -> None:
    """Charged before the call, at the worst case (every gateway retry)."""
    _LLM_CALLS.extend([now or datetime.now(UTC)] * calls)


def first_fallback(run_id: str, reason: str) -> bool:
    """True the first time this run falls back to the original text for ``reason``."""
    if (run_id, reason) in _FALLBACKS:
        return False
    _FALLBACKS.add((run_id, reason))
    return True


def reset_for_tests() -> None:
    _FALLBACKS.clear()
    _RUN_GENERATIONS.clear()
    _RUN_OWNER.clear()
    _STARTING.clear()
    _EDITING.clear()
    _LLM_CALLS.clear()
