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
import math
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal

from core.config import settings
from schemas.neurocomment_discovery import DiscoveryRunReport
from schemas.neurocomment_discovery_progress import DiscoveryStream, DiscoveryWork

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from schemas.neurocomment_discovery import (
        DiscoveryChannelVerdict,
        DiscoveryPhase,
        DiscoveryStartStatus,
    )
    from services.neurocomment._discovery_pool import AccountPool

_SEARCH_WINDOW = timedelta(hours=24)
# A stream is still going somewhere — the mean-pace ETA fallback splits the wait
# across these, and ``finish_work`` folds every one of them into "done".
_ACTIVE_STREAM_STATES = frozenset({"idle", "waiting", "reading"})

# One run per campaign (single-flight), keyed by campaign_id.
_TASKS: dict[str, asyncio.Task[None]] = {}
# Slots claimed by a start that has not reached ``spawn`` yet. Without a claim, two
# concurrent starts both pass the is-running check and the loser's task overwrites
# the winner in _TASKS, becoming untrackable — two paced RPC streams on one account,
# racing over the same candidate rows, and unreachable by shutdown. The caller must
# resolve its account BEFORE claiming, so that everything from ``try_reserve`` to
# ``spawn`` is await-free; that is what makes the claim atomic.
_RESERVED: dict[str, datetime] = {}
# The accounts each in-flight run is reading with. Per-campaign single-flight alone
# does not deliver the one-paced-stream invariant, because two campaigns can pick the
# same accounts: N campaigns would mean N streams on one account.
_RUN_ACCOUNTS: dict[str, frozenset[str]] = {}
_PHASES: dict[str, DiscoveryPhase] = {}
_LAST_ERRORS: dict[str, str] = {}
# Per-source outcome plus per-row provenance of the last run. Not persisted, for the same
# reason nothing else here is — and because the candidate table has no column for the
# provenance, which a migration against the live database would need operator approval for.
_REPORTS: dict[str, DiscoveryRunReport] = {}
# Per-candidate fitness verdicts from the qualification pass, keyed campaign -> channel.
# Ephemeral for the same reason as _REPORTS, and it degrades the same way: a board read
# after a restart finds none and reports the candidate's fitness as unknown rather than
# as fine. Written one channel at a time (unlike the report, which the search stage
# hands over whole), so it is its own map instead of a field on DiscoveryRunReport.
_VERDICTS: dict[str, dict[str, DiscoveryChannelVerdict]] = {}
# Rolling-24h timestamps of started runs, fleet-wide.
_SEARCH_TIMES: deque[datetime] = deque()
# The running (or just-finished) stage's live progress, one tracker per campaign.
# Ephemeral like everything else here: a restart loses the in-flight bar, not the run
# itself, which the operator restarts by clicking Search again.
_WORK: dict[str, WorkTracker] = {}


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


def bump_filtered(campaign_id: str, reason: str) -> None:
    """Count one row the qualification pass dropped under an operator filter."""
    filtered = _REPORTS.setdefault(campaign_id, DiscoveryRunReport()).filtered
    filtered[reason] = filtered.get(reason, 0) + 1


def verdicts(campaign_id: str) -> dict[str, DiscoveryChannelVerdict]:
    """Fitness verdicts this process's qualification passes recorded, keyed by channel.

    Empty for a campaign whose run predates this process — the caller must read a
    missing entry as "unknown", never as "fit".
    """
    return _VERDICTS.get(campaign_id, {})


def record_verdict(campaign_id: str, channel: str, verdict: DiscoveryChannelVerdict) -> None:
    _VERDICTS.setdefault(campaign_id, {})[channel] = verdict


def clear_verdicts(campaign_id: str) -> None:
    """Drop the previous run's verdicts. Called when a new run starts.

    They are per-run: a channel a previous run probed and this one did not find would
    otherwise keep a verdict nothing in this run stands behind — and the map would never
    shrink.
    """
    _VERDICTS.pop(campaign_id, None)


@dataclass(slots=True)
class WorkTracker:
    """The mutable side of one stage's live progress — what ``DiscoveryWork`` snapshots.

    One tracker per campaign, replaced at the start of each stage: a fresh one for
    ``"searching"``, another for ``"qualifying"``. ``services.neurocomment.
    _discovery_streams.Streams`` writes ``done``/``queued``/``inflight``/``extra`` and
    mutates ``streams`` in place as each account's stream advances; nothing here talks
    to Telegram or does the scheduling itself.
    """

    stage: Literal["searching", "qualifying"]
    started_at: datetime
    streams: dict[str, DiscoveryStream]
    done: int = 0
    queued: int = 0
    inflight: int = 0
    # Reads planned but not yet turned into jobs — the recommendation wave, held back
    # until the keyword sweep seeds it. Falls to 0 once those jobs exist; from then on
    # they are counted via ``queued``/``inflight``/``done`` instead.
    extra: int = 0
    finished: bool = False

    def snapshot(self) -> DiscoveryWork:
        """The immutable progress the board hands the SPA right now."""
        planned = self.done + self.inflight + self.queued + self.extra
        return DiscoveryWork(
            stage=self.stage,
            done=self.done,
            planned=planned,
            eta_seconds=self._eta_seconds(planned),
            started_at=self.started_at.isoformat(),
            streams=list(self.streams.values()),
        )

    def _eta_seconds(self, planned: int) -> int | None:
        """Rough time left: past pace once it has any, the configured pace before that.

        ``None`` once the stage is finished or nothing is left to do — an ETA next to a
        static bar reads as a stall the operator should worry about.
        """
        remaining = planned - self.done
        if self.finished or remaining <= 0:
            return None
        if self.done > 0:
            elapsed = (datetime.now(UTC) - self.started_at).total_seconds()
            per_read = elapsed / self.done
        else:
            neuro = settings.neurocomment
            mean_pace = (
                neuro.discovery_qualify_delay_min_seconds
                + neuro.discovery_qualify_delay_max_seconds
            ) / 2
            active = sum(
                1 for stream in self.streams.values() if stream.state in _ACTIVE_STREAM_STATES
            )
            per_read = mean_pace / max(active, 1)
        return math.ceil(remaining * per_read)


def start_work(
    campaign_id: str,
    stage: Literal["searching", "qualifying"],
    pool: AccountPool,
) -> WorkTracker:
    """Start tracking this campaign's live progress for a stage that is about to run.

    Replaces any tracker the previous stage left — a stage transition shares no
    counters or streams with the one before it.
    """
    tracker = WorkTracker(
        stage=stage,
        started_at=datetime.now(UTC),
        streams={
            account.account_id: DiscoveryStream(
                account_id=account.account_id,
                name=account.name,
                premium=account.premium,
            )
            for account in pool.accounts()
        },
    )
    _WORK[campaign_id] = tracker
    return tracker


def work(campaign_id: str) -> DiscoveryWork | None:
    """The current stage's live progress, or ``None`` for a campaign with none running."""
    tracker = _WORK.get(campaign_id)
    return tracker.snapshot() if tracker is not None else None


def clear_work(campaign_id: str) -> None:
    """Drop the previous run's tracker synchronously, at start — never left to ``spawn``.

    ``start_work`` only replaces it once the spawned task actually reaches its first
    stage, which is several awaits after ``start_discovery`` already reset the phase and
    reports. A board poll in that gap would otherwise show the NEW run's phase beside
    the PREVIOUS run's live streams.
    """
    _WORK.pop(campaign_id, None)


def finish_work(campaign_id: str) -> None:
    """Mark the running stage done: no more ETA, every unsettled stream reads as finished.

    Called once the stage's own coordinator (``run_search`` / ``run_qualification``)
    returns, so a stream nothing ever moved past ``idle`` (every job went to its peers)
    does not linger as "still running" on a board that has already moved on.
    """
    tracker = _WORK.get(campaign_id)
    if tracker is None:
        return
    tracker.finished = True
    for stream in tracker.streams.values():
        if stream.state in _ACTIVE_STREAM_STATES:
            stream.state = "done"


def _prune_search_times(now: datetime) -> None:
    cutoff = now - _SEARCH_WINDOW
    while _SEARCH_TIMES and _SEARCH_TIMES[0] < cutoff:
        _SEARCH_TIMES.popleft()


def at_daily_search_cap(now: datetime | None = None) -> bool:
    """Has the fleet used up its rolling-24h operator-search allowance?"""
    moment = now or datetime.now(UTC)
    _prune_search_times(moment)
    return len(_SEARCH_TIMES) >= settings.neurocomment.discovery_max_searches_per_day


def account_busy(account_id: str, *, other_than: str | None = None) -> bool:
    """Is some in-flight run — of a campaign other than ``other_than`` — reading with this account?

    The exclusion keeps a campaign's OWN run reading as ``already_running``, not as an
    account another campaign took.
    """
    return any(
        account_id in held and campaign != other_than and is_running(campaign)
        for campaign, held in _RUN_ACCOUNTS.items()
    )


def try_reserve(
    campaign_id: str,
    account_ids: frozenset[str],
    now: datetime | None = None,
) -> DiscoveryStartStatus | None:
    """Claim the run slot for this campaign AND these accounts, plus one search.

    Returns the refusal status, or ``None`` when the claim succeeded. Contains no
    await by design — the caller must already have checked ``account_ids``, so the
    whole claim-to-spawn sequence is synchronous and cannot be straddled by a second
    start. It also means no path between the claim and ``spawn`` can fail, so there is
    nothing to refund and no release to forget.
    """
    if is_running(campaign_id) or any(account_busy(account_id) for account_id in account_ids):
        return "already_running"
    moment = now or datetime.now(UTC)
    if at_daily_search_cap(moment):
        return "daily_limit_reached"
    _RESERVED[campaign_id] = moment
    _RUN_ACCOUNTS[campaign_id] = account_ids
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
    _VERDICTS.pop(campaign_id, None)
    _WORK.pop(campaign_id, None)


async def shutdown_discovery_runs() -> None:
    """Cancel every in-flight run and await it. Called from the app lifespan."""
    tasks = list(_TASKS.values())
    _TASKS.clear()
    # Claims too: a start that died between claiming and spawning would otherwise keep
    # answering ``already_running`` for the life of the process.
    _RESERVED.clear()
    _RUN_ACCOUNTS.clear()
    # The reports pin one origin model per stored candidate; nothing outlives the loop.
    _REPORTS.clear()
    _VERDICTS.clear()
    _WORK.clear()
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
    _VERDICTS.clear()
    _SEARCH_TIMES.clear()
    _WORK.clear()
