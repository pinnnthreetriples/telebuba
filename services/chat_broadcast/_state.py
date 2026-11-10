"""In-memory run state: generations, start/edit claims, the LLM budget.

The shape of ``services.neuroshilling._state``, for the same reasons: one process, one
event loop, so a function with no ``await`` in it is atomic, and nothing here is worth
persisting — a restart is a full repair from the campaign rows.

* A run GENERATION only ever rises; Stop bumps it and every external call checks it.
* ``_RUN_OWNER`` is the run entitled to write the terminal row, so a late finisher
  cannot settle ``done`` over its successor's ``running``.
* Start and settings saves exclude each other through counted claims.
* The pauses the board's gear saved while a run works, and when each chat's current
  rest began: the run reads both instead of its start-time copy. A restart loses them
  harmlessly — the run reloads the pauses from the row, and a rest whose start is
  unknown keeps its end until the next one.
* How long the run has waited for nothing but accounts held elsewhere.
* The scheduler's lock and last pass per campaign, when each stopped account was halted
  and how many times in a row it was brought back: what the self-recovery tasks need.
"""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings

if TYPE_CHECKING:
    from schemas.chat_broadcast import ChatBroadcastPace

_RUN_GENERATIONS: dict[str, int] = {}
_RUN_OWNER: dict[str, str] = {}
_STARTING: set[str] = set()
_EDITING: dict[str, int] = {}
_LLM_CALLS: deque[datetime] = deque()
_LLM_WINDOW = timedelta(days=1)
_FALLBACKS: set[tuple[str, str]] = set()
_PACE: dict[str, ChatBroadcastPace] = {}
_REST_STARTED: dict[tuple[str, str], int] = {}
_TICK_LOCKS: dict[str, asyncio.Lock] = {}
_LAST_TICK: dict[str, int] = {}
_HALTED_AT: dict[tuple[str, str], int] = {}
_RETRIES: dict[tuple[str, str], int] = {}
_STUCK_SINCE: dict[str, int] = {}


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


def set_pace(campaign_id: str, pace: ChatBroadcastPace) -> None:
    _PACE[campaign_id] = pace


def live_pace(campaign_id: str) -> ChatBroadcastPace | None:
    """Pauses saved since the run read its settings; ``None`` = the run's own copy holds."""
    return _PACE.get(campaign_id)


def forget_pace(campaign_id: str) -> None:
    """A run about to read its settings from the row needs no override on top."""
    _PACE.pop(campaign_id, None)


def forget_campaign(campaign_id: str) -> None:
    """A run settled or a campaign deleted: its live pauses, rests and tick go too."""
    _PACE.pop(campaign_id, None)
    _LAST_TICK.pop(campaign_id, None)
    lock = _TICK_LOCKS.get(campaign_id)
    if lock is not None and not lock.locked():
        # A pass still holds it: dropping it would let the next pass run beside that one.
        del _TICK_LOCKS[campaign_id]
    _STUCK_SINCE.pop(campaign_id, None)
    for registry in (_REST_STARTED, _HALTED_AT, _RETRIES):
        for key in [key for key in registry if key[0] == campaign_id]:
            del registry[key]


def note_rest_started(campaign_id: str, chat_key: str, at_unix: int) -> None:
    _REST_STARTED[campaign_id, chat_key] = at_unix


def rest_started(campaign_id: str, chat_key: str) -> int | None:
    return _REST_STARTED.get((campaign_id, chat_key))


def rest_starts(campaign_id: str) -> dict[str, int]:
    """When each chat's rest began, for the rests this process saw begin."""
    return {chat: at for (campaign, chat), at in _REST_STARTED.items() if campaign == campaign_id}


def forget_rest(campaign_id: str, chat_key: str) -> None:
    _REST_STARTED.pop((campaign_id, chat_key), None)


def tick_lock(campaign_id: str) -> asyncio.Lock:
    """The one lock the scheduler's pass takes, per campaign."""
    return _TICK_LOCKS.setdefault(campaign_id, asyncio.Lock())


def last_tick(campaign_id: str) -> int | None:
    return _LAST_TICK.get(campaign_id)


def note_tick(campaign_id: str, at_unix: int) -> None:
    _LAST_TICK[campaign_id] = at_unix


def note_halt(campaign_id: str, account_id: str, at_unix: int) -> None:
    _HALTED_AT[campaign_id, account_id] = at_unix


def halted_at(campaign_id: str, account_id: str) -> int | None:
    return _HALTED_AT.get((campaign_id, account_id))


def retries(campaign_id: str, account_id: str) -> int:
    """How many times in a row this halted account has been brought back."""
    return _RETRIES.get((campaign_id, account_id), 0)


def note_retry(campaign_id: str, account_id: str) -> None:
    _RETRIES[campaign_id, account_id] = retries(campaign_id, account_id) + 1


def reset_retries(campaign_id: str, account_id: str | None = None) -> None:
    """A send went out, or the operator pressed Start: the count of tries starts over."""
    if account_id is not None:
        _RETRIES.pop((campaign_id, account_id), None)
        return
    for key in [key for key in _RETRIES if key[0] == campaign_id]:
        del _RETRIES[key]


def stuck_since(campaign_id: str, now_unix: int) -> int:
    """When the run began to wait for nothing but accounts held elsewhere (``now_unix`` if now)."""
    return _STUCK_SINCE.setdefault(campaign_id, now_unix)


def clear_stuck(campaign_id: str) -> None:
    """Work or an account turned up: the wait for held accounts starts over."""
    _STUCK_SINCE.pop(campaign_id, None)


def reset_for_tests() -> None:
    _FALLBACKS.clear()
    _RUN_GENERATIONS.clear()
    _RUN_OWNER.clear()
    _STARTING.clear()
    _EDITING.clear()
    _LLM_CALLS.clear()
    _PACE.clear()
    _REST_STARTED.clear()
    _TICK_LOCKS.clear()
    _LAST_TICK.clear()
    _HALTED_AT.clear()
    _RETRIES.clear()
    _STUCK_SINCE.clear()
