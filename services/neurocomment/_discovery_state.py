"""In-memory state for channel-discovery runs.

Mirrors the task-handle idiom in :mod:`services.neurocomment._runtime`: the
handles live in the module that owns them, because rebinding a re-exported name
would not reach the defining module.

Nothing here is persisted, and that is deliberate. A search is a human button
press: if the process dies mid-run the operator clicks again, and the cached
candidate rows plus the linked-group cache make the retry nearly free. The
rolling search counter guards a cheap read nobody is trying to defeat, so a table
for it would be ceremony.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from schemas.neurocomment_discovery import DiscoveryPhase, DiscoveryStartStatus

_SEARCH_WINDOW = timedelta(hours=24)

# One run per campaign (single-flight), keyed by campaign_id.
_TASKS: dict[str, asyncio.Task[None]] = {}
# Slots claimed by a start that has not reached ``spawn`` yet, with the search
# timestamp each one consumed. Resolving the search account awaits, so without a
# claim taken before that await two concurrent starts both pass the is-running
# check; the loser's task then overwrites the winner in _TASKS and becomes
# untrackable — two paced RPC streams on one account, racing over the same
# candidate rows, and unreachable by shutdown.
_RESERVED: dict[str, datetime] = {}
_PHASES: dict[str, DiscoveryPhase] = {}
_LAST_ERRORS: dict[str, str] = {}
# Rolling-24h timestamps of started runs, fleet-wide.
_SEARCH_TIMES: deque[datetime] = deque()


def is_running(campaign_id: str) -> bool:
    if campaign_id in _RESERVED:
        # Claimed but not spawned yet: still a run in flight as far as callers care,
        # so the board never reports a gap between the claim and create_task.
        return True
    task = _TASKS.get(campaign_id)
    return task is not None and not task.done()


def phase_of(campaign_id: str) -> DiscoveryPhase:
    """Current phase, defaulting to ``idle`` for a campaign that never searched."""
    return _PHASES.get(campaign_id, "idle")


def set_phase(campaign_id: str, phase: DiscoveryPhase) -> None:
    _PHASES[campaign_id] = phase


def last_error(campaign_id: str) -> str | None:
    return _LAST_ERRORS.get(campaign_id)


def set_last_error(campaign_id: str, reason: str | None) -> None:
    if reason is None:
        _LAST_ERRORS.pop(campaign_id, None)
    else:
        _LAST_ERRORS[campaign_id] = reason


def _prune_search_times(now: datetime) -> None:
    cutoff = now - _SEARCH_WINDOW
    while _SEARCH_TIMES and _SEARCH_TIMES[0] < cutoff:
        _SEARCH_TIMES.popleft()


def at_daily_search_cap(now: datetime | None = None) -> bool:
    """Has the fleet used up its rolling-24h operator-search allowance?"""
    moment = now or datetime.now(UTC)
    _prune_search_times(moment)
    return len(_SEARCH_TIMES) >= settings.neurocomment.discovery_max_searches_per_day


def try_reserve(campaign_id: str, now: datetime | None = None) -> DiscoveryStartStatus | None:
    """Claim this campaign's run slot and one search from the rolling allowance.

    Returns the refusal status, or ``None`` when the claim succeeded. Deliberately
    contains no await: that is the whole point, and it is why both the single-flight
    check and the allowance check live here rather than being read separately by the
    caller across an await.
    """
    if is_running(campaign_id):
        return "already_running"
    moment = now or datetime.now(UTC)
    if at_daily_search_cap(moment):
        return "daily_limit_reached"
    _RESERVED[campaign_id] = moment
    _SEARCH_TIMES.append(moment)
    return None


def release(campaign_id: str) -> None:
    """Drop a claim whose run never spawned, refunding the search it consumed."""
    stamp = _RESERVED.pop(campaign_id, None)
    if stamp is not None:
        # Always present: the stamp is "now", and pruning only drops entries past 24h.
        _SEARCH_TIMES.remove(stamp)


def spawn(campaign_id: str, coro: Coroutine[None, None, None]) -> None:
    """Attach the real task to this campaign's claim, making it cancellable."""
    task = asyncio.create_task(coro)
    _TASKS[campaign_id] = task
    _RESERVED.pop(campaign_id, None)
    task.add_done_callback(lambda done: _forget(campaign_id, done))


def _forget(campaign_id: str, task: asyncio.Task[None]) -> None:
    if _TASKS.get(campaign_id) is task:
        del _TASKS[campaign_id]


async def shutdown_discovery_runs() -> None:
    """Cancel every in-flight run and await it. Called from the app lifespan."""
    tasks = list(_TASKS.values())
    _TASKS.clear()
    for task in tasks:
        task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task


def reset_for_tests() -> None:
    for task in _TASKS.values():
        task.cancel()
    _TASKS.clear()
    _RESERVED.clear()
    _PHASES.clear()
    _LAST_ERRORS.clear()
    _SEARCH_TIMES.clear()
