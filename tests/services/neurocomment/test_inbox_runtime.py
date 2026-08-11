"""Durability, overload and bounded-backfill behavior."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func, select, update

from core.config import settings
from core.db import _get_engine, enqueue_post
from core.repositories.neurocomment._tables import _neurocomment_inbox
from schemas.neurocomment_pipeline import PipelineOutcome
from schemas.telegram_actions import NewPostEvent
from services.neurocomment import _inbox_runtime, _runtime

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.usefixtures("isolate_runtime")
_WAIT_FAILED = "condition was not reached"
_UNKNOWN_SEND = "send outcome unknown"


def _event(channel: str, post_id: int, *, age: int = 0) -> NewPostEvent:
    return NewPostEvent(
        channel=channel,
        post_id=post_id,
        text="fresh",
        date_unix=int(datetime.now(UTC).timestamp()) - age,
    )


async def _wait_for(predicate: Callable[[], bool]) -> None:
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(_WAIT_FAILED)


@pytest.mark.asyncio
async def test_overload_is_queued_and_eventually_drained(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.neurocomment, "max_concurrent_post_tasks", 2)
    release = asyncio.Event()
    handled: list[int] = []

    async def _handle(event: NewPostEvent) -> None:
        await release.wait()
        handled.append(event.post_id)

    monkeypatch.setattr(_runtime, "handle_new_post", _handle)
    for post_id in range(5):
        await _runtime.on_post(_event("@news", post_id + 1))

    assert len(_runtime._TASKS) == 2
    with _get_engine().connect() as connection:
        pending = connection.execute(
            select(func.count()).where(_neurocomment_inbox.c.state == "pending"),
        ).scalar_one()
    assert pending == 3

    release.set()
    await _wait_for(lambda: len(handled) == 5)
    assert sorted(handled) == [1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_untyped_worker_failure_is_bounded_pre_send_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def _unknown(_event: NewPostEvent) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError(_UNKNOWN_SEND)

    monkeypatch.setattr(_runtime, "handle_new_post", _unknown)
    await _runtime.on_post(_event("@news", 1))
    await _wait_for(lambda: not _runtime._TASKS)
    await _inbox_runtime.start_inbox(recover_processing=True)
    await asyncio.sleep(0)

    assert calls == 1
    with _get_engine().connect() as connection:
        state = connection.execute(select(_neurocomment_inbox.c.state)).scalar_one()
    assert state == "pending"


@pytest.mark.asyncio
async def test_stop_wins_while_dispatch_claim_is_in_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = _event("@stop-race", 1)
    await enqueue_post(event)
    entered, release = asyncio.Event(), asyncio.Event()
    original_claim = _inbox_runtime.claim_pending_posts

    async def _slow_claim(limit: int, cutoff: int) -> list[NewPostEvent]:
        entered.set()
        await release.wait()
        return await original_claim(limit, cutoff)

    monkeypatch.setattr(_inbox_runtime, "claim_pending_posts", _slow_claim)
    dispatch = asyncio.create_task(_inbox_runtime._dispatch_pending())
    await entered.wait()
    stop = asyncio.create_task(_inbox_runtime.stop_inbox())
    await asyncio.sleep(0)  # Stop closes accepting immediately, then waits on the lock.
    release.set()
    await asyncio.gather(dispatch, stop)

    assert not _runtime._TASKS
    with _get_engine().connect() as connection:
        row = connection.execute(select(_neurocomment_inbox)).mappings().one()
    assert row["state"] == "pending"
    assert row["attempts"] == 0


@pytest.mark.asyncio
async def test_retry_deadline_has_owned_wakeup_without_external_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.neurocomment, "post_inbox_retry_base_seconds", 60)
    timer_started, release_timer = asyncio.Event(), asyncio.Event()
    calls = 0

    async def _sleep_until(_deadline: int) -> None:
        timer_started.set()
        await release_timer.wait()

    async def _handle(_event: NewPostEvent) -> PipelineOutcome:
        nonlocal calls
        calls += 1
        return PipelineOutcome.RETRYABLE if calls == 1 else PipelineOutcome.TERMINAL

    monkeypatch.setattr(_inbox_runtime, "_sleep_until", _sleep_until)
    monkeypatch.setattr(_runtime, "handle_new_post", _handle)
    await _runtime.on_post(_event("@retry-timer", 1))
    await asyncio.wait_for(timer_started.wait(), timeout=1)
    assert calls == 1

    # Advance the durable clock deterministically instead of sleeping for the backoff.
    with _get_engine().begin() as connection:
        connection.execute(update(_neurocomment_inbox).values(next_attempt_unix=0))
    release_timer.set()
    await _wait_for(lambda: calls == 2 and not _runtime._TASKS)
    with _get_engine().connect() as connection:
        state = connection.execute(select(_neurocomment_inbox.c.state)).scalar_one()
    assert state == "done"


@pytest.mark.asyncio
async def test_backfill_is_fresh_bounded_and_deduplicates_live_overlap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.neurocomment, "post_backfill_max_channels", 2)
    monkeypatch.setattr(settings.neurocomment, "post_backfill_channel_delay_seconds", 0)
    _runtime._INBOX_ACCEPTING = False
    await enqueue_post(_event("@a", 2))  # live delivery races the history page
    fetched: list[str] = []

    async def _fetch(_account: str, channel: str, *, limit: int) -> list[NewPostEvent]:
        fetched.append(channel)
        assert limit == settings.neurocomment.post_backfill_limit_per_channel
        return [_event(channel, 1, age=10_000), _event(channel, 2)]

    monkeypatch.setattr(_inbox_runtime, "fetch_recent_posts", _fetch)
    await _inbox_runtime.ensure_backfill("listener", ["@a", "@b", "@c"])
    assert _runtime._BACKFILL_TASK is not None
    await _runtime._BACKFILL_TASK

    assert fetched == ["@a", "@b"]
    await _inbox_runtime.ensure_backfill("listener", ["@a", "@b"])
    assert _runtime._BACKFILL_TASK is None  # repeated reconcile is rate-limited
    with _get_engine().connect() as connection:
        rows = connection.execute(
            select(_neurocomment_inbox.c.channel, _neurocomment_inbox.c.post_id),
        ).all()
    assert sorted(rows) == [("@a", 2), ("@b", 2)]
