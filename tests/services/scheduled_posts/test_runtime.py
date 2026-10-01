"""The in-process publisher: recovery at start, wake-ups, and a clean shutdown."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from core.config import settings
from core.repositories import scheduled_posts as repo
from schemas.scheduled_posts import SchedulePhotoRequest
from schemas.telegram_actions import ActionResult
from services import scheduled_posts
from services.scheduled_posts import _dispatch
from tests.services.scheduled_posts.helpers import in_minutes, seed_account, stored_image

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable
    from pathlib import Path


@pytest_asyncio.fixture
async def published(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[list[str]]:
    """Fake gateway that records account ids; the runtime is always shut down."""
    calls: list[str] = []

    async def _execute(
        account_id: str, action: object, *, domain: str | None = None
    ) -> ActionResult:
        del action, domain
        calls.append(account_id)
        return ActionResult(status="ok", action_type="set_profile_photo", account_id=account_id)

    async def _no_refresh(_account_id: str) -> None:
        return None

    monkeypatch.setattr(_dispatch, "execute", _execute)
    monkeypatch.setattr(_dispatch, "refresh_account_avatar", _no_refresh)
    monkeypatch.setattr(settings.scheduled_posts, "min_lead_seconds", 0)
    yield calls
    await scheduled_posts.shutdown_scheduled_posts()


async def _until(predicate: Callable[[], Awaitable[bool]]) -> None:
    """Poll a durable state the worker reaches on its own (it signals nothing)."""
    for _ in range(250):
        if await predicate():
            return
        await asyncio.sleep(0.02)
    pytest.fail("condition not reached in time")


async def _schedule(tmp_path: Path, seconds: float) -> str:
    run_at = datetime.now(UTC) + timedelta(seconds=seconds)
    read = await scheduled_posts.schedule_photo(
        "acc",
        SchedulePhotoRequest(media_id=await stored_image(tmp_path), run_at=run_at),
    )
    return read.post_id


async def _state(post_id: str) -> str | None:
    post = await repo.fetch_post(post_id)
    return None if post is None else post.state


@pytest.mark.asyncio
async def test_a_post_scheduled_while_running_is_published_on_time(
    tmp_path: Path,
    published: list[str],
) -> None:
    await seed_account()
    await scheduled_posts.start_scheduled_posts()

    post_id = await _schedule(tmp_path, 0.3)

    async def _done() -> bool:
        return await _state(post_id) == "done"

    await _until(_done)
    assert published == ["acc"]


@pytest.mark.asyncio
async def test_a_create_wakes_a_worker_asleep_on_an_empty_queue(
    tmp_path: Path,
    published: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.scheduled_posts, "max_sleep_seconds", 3600.0)
    await seed_account()
    await scheduled_posts.start_scheduled_posts()
    await asyncio.sleep(0.05)  # the worker is now waiting on its event

    post_id = await _schedule(tmp_path, 0.1)

    async def _done() -> bool:
        return await _state(post_id) == "done"

    await _until(_done)
    assert published == ["acc"]


@pytest.mark.asyncio
async def test_start_recovers_interrupted_posts_and_marks_old_ones_missed(
    tmp_path: Path,
    published: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await seed_account()
    await seed_account("other")
    monkeypatch.setattr(settings.scheduled_posts, "global_gap_min_seconds", 3600.0)
    monkeypatch.setattr(settings.scheduled_posts, "global_gap_max_seconds", 3600.0)
    interrupted = await _schedule(tmp_path, 60)
    await repo.claim_one(int(time.time()) + 120)
    await repo.mark_dispatching(interrupted)
    stale = await scheduled_posts.schedule_photo(
        "other",
        SchedulePhotoRequest(media_id=await stored_image(tmp_path, "blue"), run_at=in_minutes(1)),
    )
    grace = settings.scheduled_posts.missed_grace_seconds
    monkeypatch.setattr(time, "time", lambda: datetime.now(UTC).timestamp() + grace + 3600)

    await scheduled_posts.start_scheduled_posts()

    assert await _state(interrupted) == "ambiguous"
    assert await _state(stale.post_id) == "missed"
    assert published == []


@pytest.mark.asyncio
async def test_shutdown_hands_a_post_waiting_on_pacing_back_to_the_queue(
    tmp_path: Path,
    published: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.scheduled_posts, "shutdown_drain_seconds", 0.2)
    # A gap of zero never touches the pacing clock; a tiny one makes the first
    # publish stamp it without waiting.
    monkeypatch.setattr(settings.scheduled_posts, "global_gap_min_seconds", 0.001)
    monkeypatch.setattr(settings.scheduled_posts, "global_gap_max_seconds", 0.001)
    await seed_account()
    await seed_account("other")
    first = await _schedule(tmp_path, 0.05)
    await asyncio.sleep(0.1)
    await scheduled_posts.start_scheduled_posts()

    async def _first_done() -> bool:
        return await _state(first) == "done"

    await _until(_first_done)
    # Raised only now: the first publish stamped the global slot, so the next one
    # waits an hour from THIS moment. Raised up front, the first would wait
    # `gap - uptime`, which on a freshly booted CI runner is most of an hour.
    monkeypatch.setattr(settings.scheduled_posts, "global_gap_min_seconds", 3600.0)
    monkeypatch.setattr(settings.scheduled_posts, "global_gap_max_seconds", 3600.0)
    second = await scheduled_posts.schedule_photo(
        "other",
        SchedulePhotoRequest(
            media_id=await stored_image(tmp_path, "blue"),
            run_at=datetime.now(UTC) + timedelta(seconds=0.05),
        ),
    )

    async def _second_claimed() -> bool:
        return await _state(second.post_id) == "processing"

    await _until(_second_claimed)
    await scheduled_posts.shutdown_scheduled_posts()

    after = await repo.fetch_post(second.post_id)
    assert after is not None
    assert (after.state, after.attempts) == ("pending", 0)
    assert published == ["acc"]
