"""The scheduler: one paced, concurrent stream per pool account.

A discovery stage (the keyword/seed/post/recommendation wave, or the qualification
probes) hands :class:`Streams` its jobs and lets it run them — one ``asyncio.Task`` per
:meth:`~services.neurocomment._discovery_pool.AccountPool.accounts`, each pacing only
between ITS OWN reads, sharing one queue and one read budget. Nothing here talks to
Telegram; a ``Job.run`` closure the caller built does that and reports its own outcome.
"""

from __future__ import annotations

import asyncio
import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, NamedTuple

from services.neurocomment._discovery_wave_support import READ_BUDGET, pace, skipped
from services.neurocomment._signals import signal_discovery_progress

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    from schemas.neurocomment_discovery import DiscoverySource
    from schemas.neurocomment_discovery_progress import DiscoveryStream
    from services.neurocomment._discovery_pool import AccountPool, SearchAccount
    from services.neurocomment._discovery_providers import SourceOutcome
    from services.neurocomment._discovery_state import WorkTracker
    from services.neurocomment._discovery_wave_support import Budget

# A stream that has stopped for a reason of its own — never resumes, so its state is
# never overwritten back to "done" once the run winds down (see ``_run_stream``).
_TERMINAL_STREAM_STATES = frozenset({"flooded", "cooling", "dead", "capped", "offline"})


@dataclass(slots=True)
class Job:
    """One read a stream may take.

    ``run`` is a closure the caller built (which keyword/page/seed/candidate this is);
    the scheduler only knows when to run it, on which account, and how it counts
    against the shared budget.
    """

    source: DiscoverySource
    # ``(account_id, attempt)`` — a job retried once (``JobResult.retry``) runs again
    # with ``attempt=1``, so the closure can suppress the outcome it already reported
    # for the failed first try and report only the one that actually answers.
    run: Callable[[str, int], Awaitable[JobResult]]
    # Lowest order wins a pick: 0 keywords/probes, 1 seed, 2 posts, 3 recommendations.
    order: int = 0
    # Prefer a Premium account; a plain one takes it only once none is left.
    premium: bool = False
    # Charges the shared budget and the per-account wave ceiling. Probes pass False.
    charge: bool = True
    # Draws from the budget's fenced-off share (the recommendation wave).
    held: bool = False
    # 0 on the first try; 1 once this exact read has already been retried once on a
    # different account (see ``JobResult.retry``). Never retried twice.
    attempt: int = 0


class JobResult(NamedTuple):
    """What one job did, for the scheduler to act on."""

    flood_seconds: int | None = None
    failed: bool = False
    # Enqueued after this job — the next post page, the recommendation wave once seeded.
    followups: tuple[Job, ...] = ()
    # Stop the whole stage with this reason (the qualification error-rate rule).
    abort: str | None = None
    # Locale-neutral reason to show on the stream (``FloodWait(120s)``, …); ``None``
    # leaves whatever the stream already carries alone.
    error: str | None = None
    # A flood or an unreachable account answered nothing about the read itself — worth
    # one retry on whichever account picks the job up next. Ignored past ``attempt`` 0.
    retry: bool = False
    # The client pool could not connect the account at all (no rate limit, no reply).
    unreachable: bool = False


class Streams:
    """Run a pool's jobs to completion, one paced concurrent stream per account."""

    def __init__(
        self,
        pool: AccountPool,
        work: WorkTracker,
        *,
        budget: Budget | None = None,
        signal_every: int = 1,
    ) -> None:
        self._pool = pool
        self._work = work
        self._budget = budget
        self._signal_every = signal_every
        self._cond = asyncio.Condition()
        self._queue: list[Job] = []
        self._stop: str | None = None
        # The account whose drop (or exception) produced ``self._stop`` — ``None`` for
        # an abort reason, which is not any one account's drop. Lets a caller with its
        # own per-account bookkeeping (qualification's error-rate rule) recover the
        # SPECIFIC reason that account earned, instead of the generic stop category.
        self.stopped_by: str | None = None
        # Insertion-ordered set: one skipped-source row per source, how ever many jobs
        # of it were dropped.
        self._truncated_sources: dict[DiscoverySource, None] = {}

    async def run(self, jobs: Iterable[Job]) -> str | None:
        """Run every job, then every followup, until the queue and the pool agree it's over.

        Returns the reason the pool emptied (``flooded``/``cooling``/``aborted``) or a
        job's own ``abort`` reason — or ``None`` when the streams simply ran out of work.
        """
        self._queue = list(jobs)
        self._work.queued = len(self._queue)
        tasks = [asyncio.create_task(self._worker(account)) for account in self._pool.accounts()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        signal_discovery_progress()
        for outcome in results:
            if isinstance(outcome, BaseException):
                raise outcome
        return self._stop

    def truncated(self) -> list[SourceOutcome]:
        """Sources left unread, once each.

        Either a budget that ran dry, or a queue abandoned with nothing left to pick it
        up — never rows a stop already has its own story (``pool.report``'s reason). A
        retry still waiting for a home (``attempt`` 1) is neither: it is not the budget
        that left it unread, so it is excluded rather than mislabelled ``read_budget``.
        """
        sources = dict(self._truncated_sources)
        if self._stop is None:
            for job in self._queue:
                if job.attempt == 0:
                    sources.setdefault(job.source, None)
        return [skipped(source, READ_BUDGET, truncated=True) for source in sources]

    # -- per-account worker -------------------------------------------------------

    async def _worker(self, account: SearchAccount) -> None:
        """Wrap one stream so an unexpected exception ends only this stream, not its peers."""
        stream = self._work.streams[account.account_id]
        try:
            await self._run_stream(account, stream)
        except Exception as exc:  # a bug in one stream must not hang the rest
            async with self._cond:
                stream.state = "dead"
                stream.error = type(exc).__name__
                self._set_stop(type(exc).__name__, account.account_id)
                self._cond.notify_all()
            raise

    async def _run_stream(self, account: SearchAccount, stream: DiscoveryStream) -> None:
        account_id = account.account_id
        first = True
        while True:
            job = await self._next_job(account)
            if job is None:
                break
            async with self._cond:
                stream.state = "waiting"
            if first:
                first = False
            else:
                await pace()
            # Budget exactness: a peer can spend the shared budget during THIS pace
            # sleep, so what looked available at pick time may be gone now. Recheck
            # before spending a real read — the sleep already happened, the read must
            # not (see ``_next_job``'s own use of the same check, before any sleep).
            if self._exhausted(job):
                async with self._cond:
                    self._work.inflight -= 1
                    self._leftover(job)
                    self._cond.notify_all()
                continue
            # AFTER the pace sleep, like the old single-stream ``acquire``: the comment
            # engine (or this run's own other streams) can park the account any time.
            status = self._pool.check(account_id, charge=job.charge)
            if status != "ok":
                await self._on_refused(account_id, job, stream, status)
                break
            result = await self._read(account_id, job, stream)
            if await self._finish_job(account_id, job, result, stream):
                break
        async with self._cond:
            if stream.state not in _TERMINAL_STREAM_STATES:
                stream.state = "done"
            self._cond.notify_all()

    async def _on_refused(
        self,
        account_id: str,
        job: Job,
        stream: DiscoveryStream,
        status: Literal["cooling", "capped"],
    ) -> None:
        """The account may not take this read right now: put it back, mark the stream."""
        async with self._cond:
            self._work.inflight -= 1
            self._enqueue(job)
            stream.state = status
            if status == "cooling" and self._pool.empty:
                self._set_stop("cooling", account_id)
            self._cond.notify_all()

    async def _read(self, account_id: str, job: Job, stream: DiscoveryStream) -> JobResult:
        """Run the job and account for the read either way.

        ``inflight`` was already raised the instant the job left the queue, in
        ``_next_job`` — a job is invisible to nobody between being popped and landing
        here, or a peer whose own queue is momentarily empty can observe (queue empty,
        inflight == 0) mid-pick and exit for good while this job is still about to
        produce more work. This only lowers it back down if the job's own closure
        raises before ``_finish_job`` gets a chance to. On success ``_finish_job``
        clears it, in the SAME lock acquisition it enqueues any followups in: a peer
        with nothing of its own to pick must see either "still inflight" or "the
        followup is already queued", never a gap between the two where it wrongly
        concludes there is nothing left at all.
        """
        if job.charge and self._budget is not None:
            self._budget.take(held=job.held)
        async with self._cond:
            stream.state = "reading"
        try:
            result = await job.run(account_id, job.attempt)
        except BaseException:
            async with self._cond:
                self._work.inflight -= 1
                self._cond.notify_all()
            raise
        stream.reads += 1
        return result

    async def _finish_job(
        self,
        account_id: str,
        job: Job,
        result: JobResult,
        stream: DiscoveryStream,
    ) -> bool:
        """Report the read's outcome and fold it into the shared state.

        Returns whether this stream must stop (the account it was reading with is gone).
        """
        stop = await self._pool.report(
            account_id,
            flood_seconds=result.flood_seconds,
            failed=result.failed,
            unreachable=result.unreachable,
        )
        dropped = not self._pool.has(account_id)
        async with self._cond:
            self._work.inflight -= 1
            self._work.done += 1
            for followup in result.followups:
                self._enqueue(followup)
            if result.retry and job.attempt == 0:
                # A different account (or the same one, later) gets one more try; not
                # ``truncated()``'s business — see there for why. ``held=False``: the
                # first attempt already consumed this seed's reserved read, and a second
                # draw on the reserve sent ``Budget.held`` negative — which loosened the
                # budget check for EVERY regular job after it.
                self._enqueue(dataclasses.replace(job, attempt=1, held=False))
            if result.error is not None:
                stream.error = result.error
            if result.abort is not None:
                self._set_stop(result.abort)
            if dropped:
                if result.unreachable:
                    stream.state = "offline"
                else:
                    stream.state = "flooded" if result.flood_seconds is not None else "dead"
                if stop is not None:
                    self._set_stop(stop, account_id)
            due_signal = self._work.done % self._signal_every == 0
            self._cond.notify_all()
        if due_signal:
            signal_discovery_progress()
        return dropped

    # -- shared queue, guarded by ``self._cond`` -----------------------------------

    async def _next_job(self, account: SearchAccount) -> Job | None:
        """The next job this account may take, or ``None`` once there is truly nothing left.

        Drops (never requeues) any job the shared budget has run dry for — that read is
        simply not spent, tracked for :meth:`truncated` — and keeps looking for another
        one this account can take instead.
        """
        async with self._cond:
            while True:
                if self._stop is not None:
                    return None
                job = self._eligible_job(account)
                if job is None:
                    if not self._queue and self._work.inflight == 0:
                        return None
                    await self._cond.wait()
                    continue
                self._pop(job)
                # Counted in-flight the instant it leaves the queue, not only once
                # ``_read`` gets to it: a peer's exit test (queue empty, inflight == 0)
                # must never pass while this job is still alive between here and there
                # — see ``_read`` for the hang that gap caused.
                self._work.inflight += 1
                self._cond.notify_all()
                if self._exhausted(job):
                    self._work.inflight -= 1
                    self._leftover(job)
                    continue
                return job

    def _exhausted(self, job: Job) -> bool:
        """Would taking this job overdraw the shared budget right now?

        Shared by ``_next_job`` (checked before any sleep) and ``_run_stream`` (checked
        again right after THIS stream's own pace sleep, since a peer can spend the
        budget during that sleep — see the callers for why each check is needed).
        """
        return job.charge and self._budget is not None and self._budget.exhausted_for(held=job.held)

    def _eligible(self, job: Job, account: SearchAccount) -> bool:
        """A non-premium account only takes a Premium-preferring job once none is left.

        ``premium_left`` is read live off the pool's own read counts, not off which
        streams are still running — so this flips the moment the last Premium account
        caps out, not only once it floods or dies.
        """
        return not job.premium or bool(account.premium) or not self._pool.premium_left()

    def _eligible_job(self, account: SearchAccount) -> Job | None:
        eligible = [job for job in self._queue if self._eligible(job, account)]
        return min(eligible, key=lambda job: job.order, default=None)

    def _pop(self, job: Job) -> None:
        index = next(i for i, queued in enumerate(self._queue) if queued is job)
        del self._queue[index]
        self._work.queued = len(self._queue)

    def _enqueue(self, job: Job) -> None:
        self._queue.append(job)
        self._work.queued = len(self._queue)

    def _leftover(self, job: Job) -> None:
        self._truncated_sources.setdefault(job.source, None)

    def _set_stop(self, reason: str, account_id: str | None = None) -> None:
        """First reason wins — later ones are noise once the run is already stopping."""
        if self._stop is None:
            self._stop = reason
            self.stopped_by = account_id
