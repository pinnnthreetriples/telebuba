"""Durable neurocomment inbox regression tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import inspect, select

from core.db import (
    _get_engine,
    checkpoint_backfill,
    claim_pending_posts,
    complete_post,
    enqueue_post,
    prepare_backfill,
    requeue_processing_posts,
)
from core.migration_steps_neurocomment import _add_neurocomment_inbox
from core.repositories.neurocomment._inbox import BackfillPlan
from core.repositories.neurocomment._tables import _neurocomment_cursors, _neurocomment_inbox
from schemas.telegram_actions import NewPostEvent

if TYPE_CHECKING:
    from tests.core.conftest import _EngineFactory


def _event(post_id: int, *, date_unix: int = 1_700_000_000) -> NewPostEvent:
    return NewPostEvent(
        channel="@news", post_id=post_id, text=f"post {post_id}", date_unix=date_unix
    )


def test_inbox_migration_creates_both_tables_index_and_stamp() -> None:
    engine = _get_engine()
    inspector = inspect(engine)
    assert {"neurocomment_inbox", "neurocomment_cursors"} <= set(inspector.get_table_names())
    assert "ix_nc_inbox_state_date" in {
        str(index["name"]) for index in inspector.get_indexes("neurocomment_inbox")
    }
    with engine.connect() as connection:
        name = connection.exec_driver_sql(
            "SELECT name FROM schema_version WHERE version = 53",
        ).scalar_one()
    assert name == "add_neurocomment_inbox"


def test_inbox_migration_backfills_existing_waiting_reply_state(
    legacy_engine: _EngineFactory,
) -> None:
    engine = legacy_engine("pre-inbox-reply.db")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE neurocomment_comments (channel VARCHAR, post_id INTEGER, "
            "status VARCHAR, created_at VARCHAR, updated_at VARCHAR)",
        )
        connection.exec_driver_sql(
            "CREATE TABLE neurocomment_settings (id INTEGER PRIMARY KEY, "
            "reply_wait_minutes INTEGER NOT NULL)",
        )
        connection.exec_driver_sql("INSERT INTO neurocomment_settings VALUES (1, 25)")
        connection.exec_driver_sql(
            "INSERT INTO neurocomment_comments VALUES "
            "('@chan', 1, 'waiting', '2026-01-01T00:00:00+00:00', "
            "'2026-01-01T00:00:00+00:00')",
        )

        _add_neurocomment_inbox(connection)
        _add_neurocomment_inbox(connection)

        row = connection.exec_driver_sql(
            "SELECT reply_state, reply_stage, reply_outcome, reply_attempts, "
            "reply_deadline_at FROM neurocomment_comments",
        ).one()
    assert row == ("waiting", "waiting", None, 0, "2026-01-01T00:25:00.000+00:00")


@pytest.mark.asyncio
async def test_enqueue_deduplicates_and_cursor_never_moves_backwards() -> None:
    assert await enqueue_post(_event(10)) is True
    assert await enqueue_post(_event(10)) is False
    assert await enqueue_post(_event(9)) is True

    with _get_engine().connect() as connection:
        count = connection.execute(select(_neurocomment_inbox)).all()
        cursor = connection.execute(select(_neurocomment_cursors)).mappings().one()
    assert len(count) == 2
    assert cursor["last_post_id"] == 10


@pytest.mark.asyncio
async def test_processing_rows_recover_after_restart_but_done_rows_do_not() -> None:
    now = int(datetime.now(UTC).timestamp())
    first, second = _event(1, date_unix=now), _event(2, date_unix=now)
    await enqueue_post(first)
    await enqueue_post(second)

    claimed = await claim_pending_posts(2, now - 1)
    assert [event.post_id for event in claimed] == [1, 2]
    await complete_post(first)

    assert await requeue_processing_posts() == (1, 0)
    replay = await claim_pending_posts(2, now - 1)
    assert [event.post_id for event in replay] == [2]


@pytest.mark.asyncio
async def test_stale_pending_history_expires_instead_of_being_commented() -> None:
    await enqueue_post(_event(1, date_unix=100))
    assert await claim_pending_posts(1, cutoff_unix=101) == []
    with _get_engine().connect() as connection:
        state = connection.execute(select(_neurocomment_inbox.c.state)).scalar_one()
    assert state == "expired"


@pytest.mark.asyncio
async def test_a_failed_backfill_pass_is_held_off_and_then_resumes_where_it_stopped() -> None:
    """``checkpoint_backfill`` is what decides whether history gets read again.

    A pass that dies partway owes two things to the next one: the page it had reached,
    so recovery does not restart from the newest post every time, and a hold-off, so an
    unreadable channel is not re-fetched by every reconcile that comes past.
    """
    await enqueue_post(_event(10))
    assert await prepare_backfill(["@news"], interval_seconds=300) == {
        "@news": BackfillPlan(floor_post_id=10, before_post_id=None),
    }

    await checkpoint_backfill("@news", before_post_id=4, success=False, retry_seconds=300)

    # Held off: the channel is simply absent from the plan, so no page is fetched at all.
    assert await prepare_backfill(["@news"], interval_seconds=300) == {}

    # The same checkpoint with its hold-off already spent. The floor is unchanged -- the
    # pass still owes everything down to post 10 -- and the resume point is the page the
    # failure stopped on, not the top of the channel.
    await checkpoint_backfill("@news", before_post_id=4, success=False, retry_seconds=0)

    assert await prepare_backfill(["@news"], interval_seconds=300) == {
        "@news": BackfillPlan(floor_post_id=10, before_post_id=4),
    }


@pytest.mark.asyncio
async def test_a_successful_backfill_pass_clears_the_cursor_and_waits_out_the_interval() -> None:
    """Success ends the recovery: nothing to resume, and no re-read until the interval."""
    await enqueue_post(_event(10))
    assert await prepare_backfill(["@news"], interval_seconds=300)
    await checkpoint_backfill("@news", before_post_id=4, success=False, retry_seconds=300)

    await checkpoint_backfill("@news", before_post_id=None, success=True, retry_seconds=300)

    assert await prepare_backfill(["@news"], interval_seconds=300) == {}
    # Zero interval isolates the second stamp: the channel plans again only because
    # success also dropped the hold-off the failed pass had written, and it starts from
    # the cursor's high-water mark with nothing left to resume.
    assert await prepare_backfill(["@news"], interval_seconds=0) == {
        "@news": BackfillPlan(floor_post_id=10, before_post_id=None),
    }
