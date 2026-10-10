"""The scheduler: one paced stream per account over one shared queue of page reads.

Shape copied from ``services.neurocomment._discovery_streams``, cut down to what a parser
run needs. A job is one page; its handler (``_jobs``) reads it and hands back the next
page as a followup, so a source's pages run one after another while different sources run
on different accounts at once.

An account leaves the stream when Telegram stops it:

* a flood up to ``flood_sit_out_max_seconds`` is sat out — the account is out of the pool
  until it expires, then takes jobs again;
* a longer flood, ``max_consecutive_errors`` failed reads in a row, a cooldown another
  feature recorded, or a client that never connected take it out for the rest of the run.

A page a flood or a dead connection answered nothing about is queued ONCE more, for
whichever account takes it next. The run ends when the queue is empty or no account is
left; the sources whose pages were still queued then are reported as such by the caller.
"""

from __future__ import annotations

import asyncio
import dataclasses
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Literal, NamedTuple

from core.config import settings
from services.user_parser import _seams

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    from schemas.user_parser_run import UserParserAccountProgress

JobKind = Literal["participants", "admins", "history", "posts", "replies"]
# Why every account left: the last one flooded, or the last one failed / never connected.
StopReason = Literal["flooded", "aborted"]
_KIND_ORDER: dict[JobKind, int] = {
    "admins": 0,
    "replies": 1,
    "participants": 2,
    "history": 2,
    "posts": 2,
}


@dataclass(slots=True)
class Job:
    """One page to read. ``offset`` is a member offset or a message id cursor."""

    source: int
    kind: JobKind
    offset: int = 0
    post_id: int | None = None
    # 1 once this exact page was already retried on another account; never retried twice.
    attempt: int = 0
    # The account whose try earned the retry: another one takes it while any is left.
    retried_from: str | None = None


class JobResult(NamedTuple):
    followups: tuple[Job, ...] = ()
    flood_seconds: int | None = None
    failed: bool = False
    # The client pool could not connect the account at all.
    unreachable: bool = False
    # The page answered nothing about itself (a flood, no connection): worth one retry.
    retry: bool = False


type Handler = Callable[[str, Job], Awaitable[JobResult]]
type Pacer = Callable[[bool], float]


@dataclass(frozen=True, slots=True)
class Hooks:
    """What the run is told as the streams go: a flood, a finished source, any page."""

    on_flood: Callable[[str, int], Awaitable[None]]
    on_source_done: Callable[[int], Awaitable[None]]
    on_page: Callable[[], None]


class Streams:
    """Run the queue to completion with one concurrent stream per account."""

    def __init__(
        self,
        accounts: list[UserParserAccountProgress],
        handler: Handler,
        pacer: Pacer,
        hooks: Hooks,
    ) -> None:
        self._accounts = accounts
        self._handler = handler
        self._pacer = pacer
        self._hooks = hooks
        self._cond = asyncio.Condition()
        self._queue: list[Job] = []
        self._pending: dict[int, int] = {}
        self._inflight = 0
        self._active = len(accounts)
        # Streams still taking pages — not dropped, not finished. A retry skips the account
        # that earned it only while another stream is here to take it.
        self._alive = len(accounts)
        self._faults: dict[str, int] = {}
        self.stop: StopReason | None = None

    async def run(self, jobs: Iterable[Job]) -> StopReason | None:
        for job in jobs:
            self._enqueue(job)
        workers = [asyncio.create_task(self._worker(account)) for account in self._accounts]
        try:
            await asyncio.gather(*workers)
        finally:
            for worker in workers:
                worker.cancel()
        return self.stop

    def unfinished_sources(self) -> set[int]:
        """Sources whose pages were still queued when the last account left."""
        return {source for source, left in self._pending.items() if left > 0}

    # -- per-account worker -------------------------------------------------------

    async def _worker(self, progress: UserParserAccountProgress) -> None:
        account_id = progress.account_id
        last_source: int | None = None
        while (job := await self._next_job(account_id)) is not None:
            if last_source is not None:
                progress.state = "waiting"
                await _seams.sleep(self._pacer(job.source != last_source))
            last_source = job.source
            if _seams.in_cooldown(account_id):
                # Somebody parked the account while it slept: give the page back, leave.
                await self._give_back(job, progress)
                return
            progress.state = "reading"
            try:
                result = await self._handler(account_id, job)
            except BaseException:
                async with self._cond:
                    self._inflight -= 1
                    self._cond.notify_all()
                raise
            progress.reads += 1
            sit_out = await self._finish(progress, job, result)
            if progress.state == "dropped":
                return
            if sit_out:
                await self._sit_out(progress, sit_out)
        progress.state = "done"
        async with self._cond:
            self._alive -= 1
            self._cond.notify_all()

    async def _sit_out(self, progress: UserParserAccountProgress, seconds: int) -> None:
        progress.state = "flooded"
        progress.flood_until = (_seams.now() + timedelta(seconds=seconds)).isoformat()
        await _seams.sleep(seconds)
        progress.state = "idle"
        progress.flood_until = None

    async def _give_back(self, job: Job, progress: UserParserAccountProgress) -> None:
        async with self._cond:
            self._inflight -= 1
            self._queue.append(job)
            self._drop(progress, "flooded")
            self._cond.notify_all()

    async def _finish(
        self, progress: UserParserAccountProgress, job: Job, result: JobResult
    ) -> int | None:
        """Fold one page's outcome in; returns seconds to sit out, if any."""
        account_id = progress.account_id
        if result.flood_seconds is not None:
            await self._hooks.on_flood(account_id, result.flood_seconds)
        sit_out: int | None = None
        async with self._cond:
            self._inflight -= 1
            for followup in result.followups:
                self._enqueue(followup)
            if result.retry and job.attempt == 0:
                self._enqueue(dataclasses.replace(job, attempt=1, retried_from=account_id))
            finished = self._source_job_done(job.source)
            if result.unreachable:
                self._drop(progress, "aborted")
            elif result.flood_seconds is not None:
                if result.flood_seconds <= settings.user_parser.flood_sit_out_max_seconds:
                    sit_out = result.flood_seconds
                else:
                    self._drop(progress, "flooded")
            elif result.failed:
                faults = self._faults.get(account_id, 0) + 1
                self._faults[account_id] = faults
                if faults >= settings.user_parser.max_consecutive_errors:
                    self._drop(progress, "aborted")
            else:
                self._faults[account_id] = 0
            self._cond.notify_all()
        if finished:
            await self._hooks.on_source_done(job.source)
        self._hooks.on_page()
        return sit_out

    def _drop(self, progress: UserParserAccountProgress, reason: StopReason) -> None:
        progress.state = "dropped"
        self._active -= 1
        self._alive -= 1
        if self._active <= 0 and self.stop is None:
            self.stop = reason

    # -- shared queue, guarded by ``self._cond`` -----------------------------------

    async def _next_job(self, account_id: str) -> Job | None:
        async with self._cond:
            while True:
                if self.stop is not None:
                    return None
                eligible = [job for job in self._queue if self._may_take(job, account_id)]
                if eligible:
                    job = min(eligible, key=lambda j: (j.source, _KIND_ORDER[j.kind]))
                    self._queue.remove(job)
                    self._inflight += 1
                    return job
                if not self._queue and self._inflight == 0:
                    return None
                await self._cond.wait()

    def _may_take(self, job: Job, account_id: str) -> bool:
        return job.retried_from != account_id or self._alive <= 1

    def _enqueue(self, job: Job) -> None:
        self._queue.append(job)
        self._pending[job.source] = self._pending.get(job.source, 0) + 1

    def _source_job_done(self, source: int) -> bool:
        """Count one of the source's pages as done; whether it was its last."""
        left = self._pending.get(source, 1) - 1
        self._pending[source] = left
        return left == 0
