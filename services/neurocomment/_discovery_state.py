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
from schemas.neurocomment_discovery import DiscoveryRunReport

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from schemas.neurocomment_discovery import DiscoveryPhase, DiscoveryStartStatus

_SEARCH_WINDOW = timedelta(hours=24)

# One run per campaign (single-flight), keyed by campaign_id.
_TASKS: dict[str, asyncio.Task[None]] = {}
# Slots claimed by a start that has not reached ``spawn`` yet. Without a claim, two
# concurrent starts both pass the is-running check and the loser's task overwrites
# the winner in _TASKS, becoming untrackable — two paced RPC streams on one account,
# racing over the same candidate rows, and unreachable by shutdown. The caller must
# resolve its account BEFORE claiming, so that everything from ``try_reserve`` to
# ``spawn`` is await-free; that is what makes the claim atomic.
_RESERVED: dict[str, datetime] = {}
# The account each in-flight run is reading with. Per-campaign single-flight alone
# does not deliver the one-paced-stream invariant, because every campaign resolves to
# the same fleet listener: N campaigns would mean N streams on one account.
_RUN_ACCOUNTS: dict[str, str] = {}
_PHASES: dict[str, DiscoveryPhase] = {}
_LAST_ERRORS: dict[str, str] = {}
# Per-source outcome plus per-row provenance/geo of the last run. Not persisted, for the
# same reason nothing else here is — and because the candidate table has no column for
# the geo, which a migration against the live database would need operator approval for.
_REPORTS: dict[str, DiscoveryRunReport] = {}
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


def run_report(campaign_id: str) -> DiscoveryRunReport:
    """The last run's per-source report, empty for a campaign that never searched."""
    return _REPORTS.get(campaign_id, DiscoveryRunReport())


def set_run_report(campaign_id: str, report: DiscoveryRunReport) -> None:
    _REPORTS[campaign_id] = report


def _prune_search_times(now: datetime) -> None:
    cutoff = now - _SEARCH_WINDOW
    while _SEARCH_TIMES and _SEARCH_TIMES[0] < cutoff:
        _SEARCH_TIMES.popleft()


def at_daily_search_cap(now: datetime | None = None) -> bool:
    """Has the fleet used up its rolling-24h operator-search allowance?"""
    moment = now or datetime.now(UTC)
    _prune_search_times(moment)
    return len(_SEARCH_TIMES) >= settings.neurocomment.discovery_max_searches_per_day


def account_busy(account_id: str) -> bool:
    """Is some in-flight run already reading with this account?"""
    return any(
        held == account_id and is_running(campaign) for campaign, held in _RUN_ACCOUNTS.items()
    )


def try_reserve(
    campaign_id: str,
    account_id: str,
    now: datetime | None = None,
) -> DiscoveryStartStatus | None:
    """Claim the run slot for this campaign AND this account, plus one search.

    Returns the refusal status, or ``None`` when the claim succeeded. Contains no
    await by design — the caller must already have resolved ``account_id``, so the
    whole claim-to-spawn sequence is synchronous and cannot be straddled by a second
    start. It also means no path between the claim and ``spawn`` can fail, so there is
    nothing to refund and no release to forget.
    """
    if is_running(campaign_id) or account_busy(account_id):
        return "already_running"
    moment = now or datetime.now(UTC)
    if at_daily_search_cap(moment):
        return "daily_limit_reached"
    _RESERVED[campaign_id] = moment
    _RUN_ACCOUNTS[campaign_id] = account_id
    _SEARCH_TIMES.append(moment)
    return None


def spawn(campaign_id: str, coro: Coroutine[None, None, None]) -> None:
    """Attach the real task to this campaign's claim, making it cancellable."""
    task = asyncio.create_task(coro)
    _TASKS[campaign_id] = task
    _RESERVED.pop(campaign_id, None)
    task.add_done_callback(lambda done: _forget(campaign_id, done))


def _forget(campaign_id: str, task: asyncio.Task[None]) -> None:
    if _TASKS.get(campaign_id) is task:
        del _TASKS[campaign_id]
        _RUN_ACCOUNTS.pop(campaign_id, None)


def cancel_campaign_run(campaign_id: str) -> None:
    """Stop a campaign's run and forget its state. Called when the campaign is deleted.

    Without this the run keeps probing for minutes on the shared listener for rows
    that no longer exist (every write a no-op), and the campaign's phase lingers.
    """
    task = _TASKS.pop(campaign_id, None)
    if task is not None:
        task.cancel()
    _RESERVED.pop(campaign_id, None)
    _RUN_ACCOUNTS.pop(campaign_id, None)
    _PHASES.pop(campaign_id, None)
    _LAST_ERRORS.pop(campaign_id, None)
    _REPORTS.pop(campaign_id, None)


async def shutdown_discovery_runs() -> None:
    """Cancel every in-flight run and await it. Called from the app lifespan."""
    tasks = list(_TASKS.values())
    _TASKS.clear()
    # Claims too: a start that died between claiming and spawning would otherwise keep
    # answering ``already_running`` for the life of the process.
    _RESERVED.clear()
    _RUN_ACCOUNTS.clear()
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
    _RUN_ACCOUNTS.clear()
    _PHASES.clear()
    _LAST_ERRORS.clear()
    _REPORTS.clear()
    _SEARCH_TIMES.clear()
