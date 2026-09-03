"""Randomised termination and accounting invariants of the discovery scheduler.

The unit tests in ``test_discovery_streams`` each pin one interleaving. The scheduler's
bugs so far were the interleavings nobody pinned — a job invisible between the queue and
the read, a retry drawing twice on the budget's reserve — so this drives the REAL
``Streams`` over random job graphs (followups, retries, premium/held/charge flags,
floods, dead sessions, aborts, small budgets and ceilings) and checks the invariants
that must hold whatever happened: it terminates, nothing is left in flight, the counters
agree with the queue, no read ran more than twice, the budget never went negative.

Seeds are fixed, so a failure names the seed to replay.
"""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from services.neurocomment import _discovery_state, _seams
from services.neurocomment._discovery_pool import AccountPool, SearchAccount
from services.neurocomment._discovery_streams import Job, JobResult, Streams
from services.neurocomment._discovery_wave_support import Budget

if TYPE_CHECKING:
    from schemas.neurocomment_discovery import DiscoverySource

pytestmark = pytest.mark.usefixtures("isolate_discovery")

_SOURCES: tuple[DiscoverySource, ...] = (
    "telegram_search",
    "telegram_posts",
    "telegram_similar",
    "telegram_recommended",
)
_SEEDS = 120
_TIMEOUT_SECONDS = 5.0
_TERMINAL = {"done", "capped", "flooded", "cooling", "dead", "offline", "idle", "waiting"}


class _Graph:
    """A random tree of jobs; ``held`` only on roots, sized into ``Budget.held`` by the caller."""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.runs: dict[int, int] = {}
        self.held = 0

    def job(self, depth: int = 0) -> Job:
        rng = self.rng
        charge = rng.random() < 0.8
        held = depth == 0 and charge and rng.random() < 0.2
        self.held += held

        async def run(account_id: str, attempt: int) -> JobResult:
            count = self.runs.get(id(run), 0) + 1
            self.runs[id(run)] = count
            assert count <= 2, f"a read ran {count} times on {account_id} (attempt {attempt})"
            await asyncio.sleep(0)
            roll = rng.random()
            followups = tuple(
                self.job(depth + 1) for _ in range(rng.randint(1, 2)) if depth < 3 and roll >= 0.28
            )
            if roll < 0.12 and attempt == 0:
                return JobResult(flood_seconds=rng.randint(5, 120), retry=True)
            if roll < 0.20 and attempt == 0:
                return JobResult(unreachable=True, retry=True)
            if roll < 0.28:
                return JobResult(failed=True, abort="too_many" if rng.random() < 0.03 else None)
            return JobResult(followups=followups)

        return Job(
            source=rng.choice(_SOURCES),
            run=run,
            order=rng.randint(0, 3),
            premium=rng.random() < 0.3,
            charge=charge,
            held=held,
        )


async def _one_seed(seed: int, monkeypatch: pytest.MonkeyPatch) -> None:
    rng = random.Random(seed)  # noqa: S311 - a replayable seed, not a secret
    accounts = [
        SearchAccount(f"acc{i}", premium=rng.random() < 0.4, name=f"acc{i}")
        for i in range(rng.randint(1, 4))
    ]
    pool = AccountPool(accounts)
    monkeypatch.setattr(settings.neurocomment, "discovery_max_reads_per_run", rng.randint(1, 4))
    monkeypatch.setattr(
        settings.neurocomment, "discovery_max_consecutive_errors", rng.randint(1, 3)
    )
    work = _discovery_state.start_work(f"c{seed}", "searching", pool)
    graph = _Graph(rng)
    jobs = [graph.job() for _ in range(rng.randint(1, 6))]
    budget = Budget(rng.randint(1, 8)) if rng.random() < 0.85 else None
    if budget is not None:
        budget.held = graph.held
    streams = Streams(pool, work, budget=budget, signal_every=rng.randint(1, 3))

    try:
        await asyncio.wait_for(streams.run(jobs), timeout=_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        msg = f"seed {seed} hung with queue {[job.source for job in streams._queue]}"
        raise AssertionError(msg) from exc

    assert work.inflight == 0, f"seed {seed}: inflight {work.inflight}"
    assert work.queued == len(streams._queue), f"seed {seed}: queued counter drifted"
    assert work.done == sum(graph.runs.values()), f"seed {seed}: done != reads"
    for account_id, stream in work.streams.items():
        assert stream.state in _TERMINAL, f"seed {seed}: {account_id} left {stream.state}"
        assert stream.state != "reading", f"seed {seed}: {account_id} left reading"
    if budget is not None:
        assert budget.left >= 0, f"seed {seed}: budget.left {budget.left}"
        assert budget.held >= 0, f"seed {seed}: budget.held {budget.held}"
    assert isinstance(streams.truncated(), list)


@pytest.mark.asyncio
async def test_random_job_graphs_always_terminate_with_consistent_accounting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def yielding_sleep(_seconds: float) -> None:
        await asyncio.sleep(0)

    monkeypatch.setattr(_seams, "sleep", yielding_sleep)
    for seed in range(_SEEDS):
        await _one_seed(seed, monkeypatch)
        _discovery_state.reset_for_tests()
