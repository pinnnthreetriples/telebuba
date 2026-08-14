"""Orchestrated regressions from the durable-inbox ownership review."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select, update

from core.config import settings
from core.db import (
    _get_engine,
    claim_comment,
    claim_pending_posts,
    create_account,
    create_campaign,
    enqueue_post,
    list_recent_logs,
    mark_inbox_stage,
    prepare_backfill,
    release_post,
    requeue_processing_posts,
)
from core.repositories.neurocomment._tables import (
    _neurocomment_comments,
    _neurocomment_cursors,
    _neurocomment_inbox,
)
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate
from schemas.neurocomment_pipeline import InboxStage, PipelineOutcome, ReleaseOutcome
from schemas.telegram_actions import NewPostEvent
from services.neurocomment import _inbox_runtime, _runtime

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.usefixtures("isolate_runtime")
_WAIT_FAILED = "condition was not reached"
_FETCH_FAILED = "temporary Telegram read failure"


def _event(channel: str, post_id: int) -> NewPostEvent:
    return NewPostEvent(
        channel=channel,
        post_id=post_id,
        text=f"post {post_id}",
        date_unix=int(datetime.now(UTC).timestamp()),
    )


async def _reported(event: str) -> list[dict[str, object]]:
    """The ``extra`` of every log entry under one event code, newest last."""
    entries = reversed(await list_recent_logs(limit=100))
    return [entry.extra for entry in entries if entry.event == event]


async def _wait_for(predicate: Callable[[], bool]) -> None:
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(_WAIT_FAILED)


async def _seed_claim(event: NewPostEvent) -> None:
    await create_account(AccountCreate(account_id="author", label="A", session_name="author"))
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await enqueue_post(event)
    assert await claim_pending_posts(1, event.date_unix - 1)
    assert await claim_comment(event.channel, event.post_id, campaign.campaign_id, "author")


@pytest.mark.asyncio
async def test_restart_releases_only_proven_pre_send_claim() -> None:
    event = _event("@pre", 1)
    await _seed_claim(event)

    assert await requeue_processing_posts() == (1, 0)
    with _get_engine().connect() as connection:
        inbox = connection.execute(select(_neurocomment_inbox)).mappings().one()
        comment = connection.execute(select(_neurocomment_comments)).first()
    assert inbox["state"] == "pending"
    assert inbox["stage"] == InboxStage.RECEIVED
    assert comment is None


@pytest.mark.asyncio
async def test_restart_fail_closes_dispatching_claim() -> None:
    event = _event("@ambiguous", 1)
    await _seed_claim(event)
    await mark_inbox_stage(event, InboxStage.DISPATCHING)

    # Counted apart from the safely requeued rows, because this is the half a restart
    # can only report: it will never resend them.
    assert await requeue_processing_posts() == (0, 1)
    with _get_engine().connect() as connection:
        inbox = connection.execute(select(_neurocomment_inbox)).mappings().one()
        comment = connection.execute(select(_neurocomment_comments)).mappings().one()
    assert inbox["state"] == "done"
    assert inbox["outcome"] == PipelineOutcome.AMBIGUOUS
    assert comment["status"] == "claimed"  # duplicate fence remains closed


@pytest.mark.asyncio
async def test_retry_transition_atomically_clears_lingering_pre_send_claim() -> None:
    event = _event("@release-fault", 1)
    await _seed_claim(event)

    # Models engine.release_claim failing before the typed RETRYABLE result reaches the
    # inbox. The inbox transition itself must clear the proven-pre-send claim atomically.
    assert await release_post(event, 5, 0.1, 1.0) == ReleaseOutcome.REQUEUED
    await asyncio.sleep(0.11)
    claimed = await claim_pending_posts(1, event.date_unix - 1)
    assert [item.post_id for item in claimed] == [1]
    with _get_engine().connect() as connection:
        assert connection.execute(select(_neurocomment_comments)).first() is None


@pytest.mark.asyncio
async def test_a_post_that_spends_its_retry_budget_says_so_before_it_leaves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The end of the retry ladder was reached in silence.

    ``release_post`` writes ``retry_exhausted`` and the runtime threw its answer away, so
    a post that failed its last allowed attempt looked exactly like one that was never
    received: the row goes to ``done`` and nothing is ever said about it again.
    """
    monkeypatch.setattr(settings.neurocomment, "post_inbox_max_attempts", 1)
    attempts = 0

    async def _always_retryable(_event: NewPostEvent) -> PipelineOutcome:
        nonlocal attempts
        attempts += 1
        return PipelineOutcome.RETRYABLE

    monkeypatch.setattr(_runtime, "handle_new_post", _always_retryable)

    await _runtime.on_post(_event("@spent", 1))
    await _wait_for(lambda: not _runtime._TASKS)

    with _get_engine().connect() as connection:
        row = connection.execute(select(_neurocomment_inbox)).mappings().one()
    assert attempts == 1  # the budget really is spent, not merely deferred
    assert row["state"] == "done"
    assert row["outcome"] == ReleaseOutcome.EXHAUSTED
    assert await _reported("neurocomment_inbox_retry_exhausted") == [
        {"channel": "@spent", "post_id": 1},
    ]


@pytest.mark.asyncio
async def test_a_worker_that_dies_after_dispatch_reports_the_post_it_cannot_retry() -> None:
    """Refusing to resend is right; refusing in silence is what left no way to look.

    ``release_post`` fails closed on a row past ``pre_send`` -- the comment may be live
    on Telegram, so no retry is allowed. The row is settled ``ambiguous`` and, before
    this, the only trace was a column no query reads.
    """
    event = _event("@half-sent", 1)
    await _seed_claim(event)
    await mark_inbox_stage(event, InboxStage.DISPATCHING)

    await _inbox_runtime._retry(event)

    with _get_engine().connect() as connection:
        row = connection.execute(select(_neurocomment_inbox)).mappings().one()
    assert row["outcome"] == PipelineOutcome.AMBIGUOUS
    assert await _reported("neurocomment_inbox_dispatch_ambiguous") == [
        {"channel": "@half-sent", "post_id": 1},
    ]


@pytest.mark.asyncio
async def test_restart_recovery_reports_the_posts_it_refuses_to_resend() -> None:
    """A crash mid-send is the one loss recovery cannot repair, so it has to name it."""
    resumable, half_sent = _event("@safe", 1), _event("@half-sent", 2)
    await _seed_claim(resumable)
    await enqueue_post(half_sent)
    assert await claim_pending_posts(1, half_sent.date_unix - 1)
    await mark_inbox_stage(half_sent, InboxStage.DISPATCHED)

    await _inbox_runtime.recover_inbox()

    # The requeued row rides along for scale, so the operator can tell one abandoned
    # post out of two from a restart that abandoned everything it found.
    assert await _reported("neurocomment_inbox_recovery_ambiguous") == [
        {"posts": 1, "requeued": 1},
    ]


@pytest.mark.asyncio
async def test_a_restart_with_nothing_mid_send_stays_quiet() -> None:
    """The report must mean something: an ordinary restart raises no alarm."""
    await _seed_claim(_event("@safe", 1))

    await _inbox_runtime.recover_inbox()

    assert await _reported("neurocomment_inbox_recovery_ambiguous") == []


@pytest.mark.asyncio
async def test_retryable_provider_fault_is_retried_then_settled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.neurocomment, "post_inbox_retry_base_seconds", 0.1)
    calls = 0

    async def _handle(_event: NewPostEvent) -> PipelineOutcome:
        nonlocal calls
        calls += 1
        return PipelineOutcome.RETRYABLE if calls == 1 else PipelineOutcome.TERMINAL

    monkeypatch.setattr(_runtime, "handle_new_post", _handle)
    await _runtime.on_post(_event("@retry", 1))
    await _wait_for(lambda: calls == 2 and not _runtime._TASKS)
    with _get_engine().connect() as connection:
        row = connection.execute(select(_neurocomment_inbox)).mappings().one()
    assert row["state"] == "done"
    assert row["attempts"] == 2


@pytest.mark.asyncio
async def test_queue_cap_rejects_newest_without_unbounded_growth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.neurocomment, "post_inbox_max_pending", 2)
    _runtime._INBOX_ACCEPTING = False

    for post_id in range(1, 4):
        await _runtime.on_post(_event("@cap", post_id))

    with _get_engine().connect() as connection:
        ids = connection.execute(select(_neurocomment_inbox.c.post_id)).scalars().all()
    assert ids == [1, 2]


@pytest.mark.asyncio
async def test_backfill_paginates_and_resumes_from_durable_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.neurocomment, "post_backfill_limit_per_channel", 2)
    monkeypatch.setattr(settings.neurocomment, "post_backfill_max_pages_per_channel", 2)
    monkeypatch.setattr(settings.neurocomment, "post_backfill_channel_delay_seconds", 0)
    _runtime._INBOX_ACCEPTING = False
    await enqueue_post(_event("@pages", 1))
    calls: list[int | None] = []

    async def _fetch(
        _account: str,
        channel: str,
        *,
        limit: int,
        before_post_id: int | None = None,
    ) -> list[NewPostEvent]:
        assert channel == "@pages"
        assert limit == 2
        calls.append(before_post_id)
        pages = {None: [5, 4], 4: [3, 2], 2: [1]}
        return [_event(channel, post_id) for post_id in pages[before_post_id]]

    monkeypatch.setattr(_inbox_runtime, "fetch_recent_posts", _fetch)
    plans = await prepare_backfill(["@pages"], interval_seconds=300)
    await _inbox_runtime.ensure_backfill("listener", ["@pages"], plans)
    assert _runtime._BACKFILL_TASK is not None
    await _runtime._BACKFILL_TASK

    with _get_engine().begin() as connection:
        checkpoint = connection.execute(select(_neurocomment_cursors)).mappings().one()
        assert checkpoint["backfill_floor_post_id"] == 1
        assert checkpoint["backfill_before_post_id"] == 2
        connection.execute(
            update(_neurocomment_cursors).values(backfill_retry_at="2000-01-01T00:00:00+00:00"),
        )

    resumed = await prepare_backfill(["@pages"], interval_seconds=300)
    assert resumed["@pages"].before_post_id == 2
    await _inbox_runtime.ensure_backfill("listener", ["@pages"], resumed)
    assert _runtime._BACKFILL_TASK is not None
    await _runtime._BACKFILL_TASK

    with _get_engine().connect() as connection:
        ids = connection.execute(select(_neurocomment_inbox.c.post_id)).scalars().all()
        checkpoint = connection.execute(select(_neurocomment_cursors)).mappings().one()
    assert calls == [None, 4, 2]
    assert sorted(ids) == [1, 2, 3, 4, 5]
    assert checkpoint["backfill_floor_post_id"] is None
    assert checkpoint["backfill_success_at"] is not None


@pytest.mark.asyncio
async def test_fetch_failure_records_retry_not_success(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fail(*_args: object, **_kwargs: object) -> list[NewPostEvent]:
        raise OSError(_FETCH_FAILED)

    monkeypatch.setattr(_inbox_runtime, "fetch_recent_posts", _fail)
    plans = await prepare_backfill(["@fail"], interval_seconds=300)
    await _inbox_runtime.ensure_backfill("listener", ["@fail"], plans)
    assert _runtime._BACKFILL_TASK is not None
    await _runtime._BACKFILL_TASK

    with _get_engine().connect() as connection:
        cursor = connection.execute(select(_neurocomment_cursors)).mappings().one()
    assert cursor["backfill_success_at"] is None
    assert cursor["backfill_retry_at"] is not None


@pytest.mark.asyncio
async def test_replaced_backfill_suppressing_cancel_cannot_resume_stale_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = 0

    async def _stubborn_fetch(
        _account: str,
        channel: str,
        *,
        limit: int,
        before_post_id: int | None = None,
    ) -> list[NewPostEvent]:
        del limit, before_post_id
        nonlocal calls
        calls += 1
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
        return [_event(channel, 2)]

    monkeypatch.setattr(_inbox_runtime, "fetch_recent_posts", _stubborn_fetch)
    plans = await prepare_backfill(["@old"], interval_seconds=300)
    await _inbox_runtime.ensure_backfill("a", ["@old"], plans)
    old_task = _runtime._BACKFILL_TASK
    assert old_task is not None
    await entered.wait()

    _runtime._invalidate_runtime_owner("a")
    _runtime._activate_runtime_owner("b")
    await _inbox_runtime.ensure_backfill("b", [])
    await cancelled.wait()
    assert old_task in _runtime._RETIRED_TASKS
    release.set()
    await old_task

    with _get_engine().connect() as connection:
        rows = connection.execute(select(_neurocomment_inbox)).all()
        cursor = (
            connection.execute(
                select(_neurocomment_cursors).where(_neurocomment_cursors.c.channel == "@old"),
            )
            .mappings()
            .one()
        )
    assert calls == 1
    assert rows == []
    assert cursor["backfill_success_at"] is None
    assert cursor["backfill_before_post_id"] is None
