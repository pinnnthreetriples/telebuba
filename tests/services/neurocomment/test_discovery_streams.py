"""The scheduler: one paced, concurrent stream per pool account."""

from __future__ import annotations

import asyncio

import pytest

from core.config import settings
from services.neurocomment import _discovery_state, _seams
from services.neurocomment._discovery_pool import AccountPool, SearchAccount
from services.neurocomment._discovery_streams import Job, JobResult, Streams
from services.neurocomment._discovery_wave_support import Budget
from tests.services.neurocomment.discovery_support import pool_of

pytestmark = pytest.mark.usefixtures("isolate_discovery")


def _work(pool: AccountPool, campaign_id: str = "c") -> _discovery_state.WorkTracker:
    return _discovery_state.start_work(campaign_id, "searching", pool)


async def _yielding(_account_id: str, _attempt: int) -> JobResult:
    """A job that behaves like a real read: it actually awaits something."""
    await asyncio.sleep(0)
    return JobResult()


@pytest.mark.asyncio
async def test_two_accounts_read_concurrently_not_one_after_another() -> None:
    """Both streams' reads must be observed in flight together, not queued behind one."""
    events: list[str] = []

    async def run(account_id: str, _attempt: int) -> JobResult:
        events.append(f"{account_id}:start")
        await asyncio.sleep(0)
        events.append(f"{account_id}:end")
        return JobResult()

    pool = pool_of("a", "b")
    jobs = [Job(source="telegram_search", run=run), Job(source="telegram_search", run=run)]

    stop = await Streams(pool, _work(pool)).run(jobs)

    assert stop is None
    # Sequential execution would read a:start, a:end, b:start, b:end — both starts
    # landing before either end proves the two streams actually overlapped.
    assert events[:2] in (["a:start", "b:start"], ["b:start", "a:start"])
    assert set(events) == {"a:start", "a:end", "b:start", "b:end"}


@pytest.mark.asyncio
async def test_pacing_never_blocks_a_different_streams_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One stream paces between ITS OWN reads only — never against the other's."""
    pace_calls: list[float] = []

    async def recording_sleep(seconds: float) -> None:
        pace_calls.append(seconds)
        await asyncio.sleep(0)

    monkeypatch.setattr(_seams, "sleep", recording_sleep)
    monkeypatch.setattr(_seams.rng, "uniform", lambda low, _high: low)
    pool = pool_of("a", "b")
    jobs = [Job(source="telegram_search", run=_yielding) for _ in range(4)]

    stop = await Streams(pool, _work(pool)).run(jobs)

    assert stop is None
    # 4 reads over 2 streams is one pacing gap per stream, however the 4 jobs split.
    assert len(pace_calls) == 2


@pytest.mark.asyncio
async def test_a_premium_job_prefers_the_premium_account_then_falls_back_once_it_drops() -> None:
    pool = AccountPool([SearchAccount("plain", premium=False), SearchAccount("paid", premium=True)])
    picked: list[str] = []

    async def second(account_id: str, _attempt: int) -> JobResult:
        picked.append(account_id)
        return JobResult()

    async def first(account_id: str, _attempt: int) -> JobResult:
        picked.append(account_id)
        # Floods the Premium account, so the only account left inherits the followup.
        return JobResult(
            flood_seconds=60,
            followups=(Job(source="telegram_recommended", run=second, premium=True),),
        )

    jobs = [Job(source="telegram_recommended", run=first, premium=True)]

    stop = await Streams(pool, _work(pool)).run(jobs)

    # "plain" is never eligible for a Premium-preferring job while "paid" is still
    # usable, so the first read can only have gone to "paid"; once it drops,
    # ``premium_left`` flips and "plain" is the only account left to inherit the rest.
    assert picked == ["paid", "plain"]
    assert stop is None


@pytest.mark.asyncio
async def test_budget_exhaustion_yields_one_truncated_outcome_per_source() -> None:
    pool = pool_of("a")
    ran: list[str] = []

    async def run(account_id: str, _attempt: int) -> JobResult:
        ran.append(account_id)
        return JobResult()

    jobs = [
        Job(source="telegram_search", run=run),
        Job(source="telegram_search", run=run),
        Job(source="telegram_posts", run=run),
    ]
    streams = Streams(pool, _work(pool), budget=Budget(1))

    stop = await streams.run(jobs)

    assert stop is None
    assert ran == ["a"]  # only the one read the budget allowed actually happened
    truncated = streams.truncated()
    assert {outcome.source for outcome in truncated} == {"telegram_search", "telegram_posts"}
    assert all(outcome.truncated and outcome.error == "read_budget" for outcome in truncated)


@pytest.mark.asyncio
async def test_budget_exactness_survives_a_streams_own_pace_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A peer must not be able to overdraw the budget during this stream's pace sleep.

    ``_next_job`` only checks the budget BEFORE a stream paces; without a recheck right
    after, two streams that both passed that check before sleeping concurrently for
    their own second (charged) job would both then take a read once they wake,
    overdrawing a budget of 1 down to -1. Each account's first job is unmetered (like a
    probe) so it reaches its second, charged job without ever touching the budget
    itself — that is what lets both streams' pace sleeps genuinely overlap.
    """

    async def recording_sleep(_seconds: float) -> None:
        await asyncio.sleep(0)  # a real yield, so the two streams actually overlap

    monkeypatch.setattr(_seams, "sleep", recording_sleep)
    monkeypatch.setattr(_seams.rng, "uniform", lambda low, _high: low)
    pool = pool_of("a", "b")
    ran: list[str] = []

    async def probe(account_id: str, _attempt: int) -> JobResult:
        await asyncio.sleep(0)
        ran.append(f"probe:{account_id}")
        return JobResult()

    async def charged(account_id: str, _attempt: int) -> JobResult:
        await asyncio.sleep(0)
        ran.append(f"charged:{account_id}")
        return JobResult()

    jobs = [
        Job(source="telegram_search", run=probe, order=0, charge=False),
        Job(source="telegram_search", run=probe, order=0, charge=False),
        Job(source="telegram_search", run=charged, order=1, charge=True),
        Job(source="telegram_search", run=charged, order=1, charge=True),
    ]
    budget = Budget(1)
    streams = Streams(pool, _work(pool), budget=budget)

    stop = await streams.run(jobs)

    assert stop is None
    assert sum(1 for entry in ran if entry.startswith("charged:")) == 1
    assert budget.left == 0  # never overdrawn negative
    assert [outcome.source for outcome in streams.truncated()] == ["telegram_search"]


@pytest.mark.asyncio
async def test_a_flood_drops_one_account_and_the_other_finishes_the_queue() -> None:
    pool = pool_of("a", "b")
    ran: list[str] = []
    flooded = False

    async def run(account_id: str, _attempt: int) -> JobResult:
        nonlocal flooded
        ran.append(account_id)
        await asyncio.sleep(0)
        if not flooded:
            flooded = True
            return JobResult(flood_seconds=60)
        return JobResult()

    jobs = [Job(source="telegram_search", run=run) for _ in range(3)]

    stop = await Streams(pool, _work(pool)).run(jobs)

    assert stop is None
    assert len(ran) == 3
    assert len(pool.accounts()) == 1


@pytest.mark.asyncio
async def test_both_accounts_dropping_stops_the_run_and_abandons_the_queue() -> None:
    pool = pool_of("a", "b")

    async def flood_run(_account_id: str, _attempt: int) -> JobResult:
        await asyncio.sleep(0)
        return JobResult(flood_seconds=60)

    async def never_run(_account_id: str, _attempt: int) -> JobResult:
        message = "must never run: the pool emptied before this was reached"
        raise AssertionError(message)

    jobs = [Job(source="telegram_search", run=flood_run) for _ in range(2)] + [
        Job(source="telegram_search", run=never_run) for _ in range(5)
    ]

    stop = await Streams(pool, _work(pool)).run(jobs)

    assert stop == "flooded"
    assert pool.empty is True


@pytest.mark.asyncio
async def test_a_capped_account_exits_and_the_others_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.neurocomment, "discovery_max_reads_per_run", 1)
    pool = pool_of("a", "b")
    ran: list[str] = []

    async def run(account_id: str, _attempt: int) -> JobResult:
        ran.append(account_id)
        await asyncio.sleep(0)
        return JobResult()

    jobs = [Job(source="telegram_search", run=run) for _ in range(3)]
    streams = Streams(pool, _work(pool))

    stop = await streams.run(jobs)

    assert stop is None
    # Each account's own ceiling (1) is spent; the 3rd job is left for qualification's
    # probes (bounded elsewhere), not for this wave.
    assert len(ran) == 2
    assert len(pool.accounts()) == 2
    assert [outcome.source for outcome in streams.truncated()] == ["telegram_search"]


@pytest.mark.asyncio
async def test_followups_are_enqueued_and_run() -> None:
    pool = pool_of("a")
    ran: list[str] = []

    async def second(_account_id: str, _attempt: int) -> JobResult:
        ran.append("second")
        return JobResult()

    async def first(_account_id: str, _attempt: int) -> JobResult:
        ran.append("first")
        return JobResult(followups=(Job(source="telegram_posts", run=second),))

    stop = await Streams(pool, _work(pool)).run([Job(source="telegram_posts", run=first)])

    assert stop is None
    assert ran == ["first", "second"]


@pytest.mark.asyncio
async def test_a_jobs_abort_stops_the_whole_run() -> None:
    pool = pool_of("a")
    ran: list[str] = []

    async def aborting(account_id: str, _attempt: int) -> JobResult:
        ran.append(account_id)
        return JobResult(abort="too_many_errors")

    async def never(_account_id: str, _attempt: int) -> JobResult:
        message = "must never run: the abort must stop the stage first"
        raise AssertionError(message)

    jobs = [Job(source="telegram_search", run=aborting, charge=False)] + [
        Job(source="telegram_search", run=never, charge=False) for _ in range(3)
    ]

    stop = await Streams(pool, _work(pool)).run(jobs)

    assert stop == "too_many_errors"
    assert ran == ["a"]


@pytest.mark.asyncio
async def test_stream_error_is_recorded_from_the_job_result() -> None:
    pool = pool_of("a")
    work = _work(pool)

    async def run(_account_id: str, _attempt: int) -> JobResult:
        return JobResult(error="FloodWait(120s)")

    await Streams(pool, work).run([Job(source="telegram_search", run=run)])

    assert work.streams["a"].error == "FloodWait(120s)"


@pytest.mark.asyncio
async def test_an_unexpected_exception_marks_its_own_stream_dead_and_stops_the_run() -> None:
    pool = pool_of("a")
    work = _work(pool)

    async def broken(_account_id: str, _attempt: int) -> JobResult:
        message = "boom"
        raise ValueError(message)

    with pytest.raises(ValueError, match="boom"):
        await Streams(pool, work).run([Job(source="telegram_search", run=broken)])

    assert work.streams["a"].state == "dead"
    assert work.streams["a"].error == "ValueError"


@pytest.mark.asyncio
async def test_an_exception_in_one_stream_does_not_hang_its_peers() -> None:
    pool = pool_of("a", "b")
    ran: list[str] = []
    broke = False

    async def run(account_id: str, _attempt: int) -> JobResult:
        nonlocal broke
        ran.append(account_id)
        await asyncio.sleep(0)
        if not broke:
            broke = True
            message = "boom"
            raise ValueError(message)
        return JobResult()

    jobs = [Job(source="telegram_search", run=run) for _ in range(3)]

    with pytest.raises(ValueError, match="boom"):
        await Streams(pool, _work(pool)).run(jobs)

    # The surviving stream kept reading instead of hanging forever behind the crash.
    assert len(ran) >= 2


@pytest.mark.asyncio
async def test_work_tracker_counts_done_and_final_stream_states() -> None:
    pool = pool_of("a", "b")
    work = _work(pool)
    jobs = [Job(source="telegram_search", run=_yielding) for _ in range(4)]

    stop = await Streams(pool, work).run(jobs)

    assert stop is None
    assert work.done == 4
    assert work.queued == 0
    assert work.inflight == 0
    snapshot = work.snapshot()
    assert snapshot.done == 4
    assert {stream.account_id for stream in snapshot.streams} == {"a", "b"}
    assert all(stream.state == "done" for stream in snapshot.streams)


@pytest.mark.asyncio
async def test_an_unreachable_account_drops_at_once_and_the_job_retries_elsewhere() -> None:
    """No three strikes for a client that never connected — one read, then gone."""
    pool = pool_of("a", "b")
    work = _work(pool)
    calls: list[str] = []

    async def run(account_id: str, attempt: int) -> JobResult:
        calls.append(account_id)
        if account_id == "a":
            return JobResult(unreachable=True, retry=attempt == 0)
        return JobResult()

    stop = await Streams(pool, work).run([Job(source="telegram_search", run=run)])

    assert stop is None
    assert calls.count("a") == 1
    assert calls.count("b") == 1
    assert pool.has("a") is False
    assert work.streams["a"].state == "offline"


@pytest.mark.asyncio
async def test_both_accounts_unreachable_stops_the_run_aborted() -> None:
    pool = pool_of("a", "b")
    work = _work(pool)

    async def run(_account_id: str, attempt: int) -> JobResult:
        return JobResult(unreachable=True, retry=attempt == 0)

    jobs = [Job(source="telegram_search", run=run) for _ in range(2)]

    stop = await Streams(pool, work).run(jobs)

    assert stop == "aborted"
    assert pool.empty is True


@pytest.mark.asyncio
async def test_a_premium_followup_from_the_last_standing_plain_job_is_not_stranded() -> None:
    """A job counts as in-flight the instant it is popped, not only once ``_read`` gets it.

    Regression for a real hang: between a job leaving the queue in ``_next_job`` and
    ``_read`` raising ``inflight``, a peer whose own queue looked momentarily empty
    could see (queue empty, inflight == 0) and exit for good — stranding a
    Premium-only followup a still-alive plain job was about to enqueue, since
    ``premium_left()`` was still True and the plain stream was never eligible for it.
    """
    pool = AccountPool(
        [SearchAccount("plain", premium=False), SearchAccount("premium", premium=True)]
    )
    work = _work(pool)
    ran: list[str] = []

    async def followup_run(account_id: str, _attempt: int) -> JobResult:
        ran.append(f"followup:{account_id}")
        return JobResult()

    async def j2_run(_account_id: str, _attempt: int) -> JobResult:
        ran.append("j2")
        followup = Job(source="telegram_recommended", run=followup_run, order=9, premium=True)
        return JobResult(followups=(followup,))

    async def j1_run(_account_id: str, _attempt: int) -> JobResult:
        ran.append("j1")
        return JobResult()

    async def decoy_run(_account_id: str, _attempt: int) -> JobResult:
        ran.append("decoy")
        return JobResult()

    jobs = [
        Job(source="telegram_recommended", run=decoy_run, order=-1, premium=True),
        Job(source="telegram_search", run=j1_run, order=0),
        Job(source="telegram_search", run=j2_run, order=0),
    ]

    stop = await asyncio.wait_for(Streams(pool, work).run(jobs), timeout=5)

    assert stop is None
    assert "followup:premium" in ran


@pytest.mark.asyncio
async def test_a_retried_held_job_draws_on_the_reserve_only_once() -> None:
    """One seed = one reserved read. Its retry charges the budget, not the reserve again.

    The retry clone inherited ``held=True`` and ``Budget.take`` subtracted a second time,
    so ``held`` went negative — and a negative reserve made ``left - held`` LARGER, letting
    every regular job past a budget that was already spent.
    """
    pool = pool_of("a", "b")
    budget = Budget(3)
    budget.held = 1
    attempts: list[int] = []

    async def flood_once(_account_id: str, attempt: int) -> JobResult:
        attempts.append(attempt)
        await asyncio.sleep(0)
        if attempt == 0:
            return JobResult(flood_seconds=30, retry=True)
        return JobResult()

    stop = await Streams(pool, _work(pool), budget=budget).run(
        [Job(source="telegram_recommended", run=flood_once, held=True)],
    )

    assert stop is None
    assert attempts == [0, 1]
    assert budget.held == 0
    assert budget.left == 1
