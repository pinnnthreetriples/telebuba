"""Scheduled-post rows: every transition is a compare-and-set, recovery never repeats."""

from __future__ import annotations

import time
import uuid

import pytest

from core.db import _get_engine, create_account, delete_account
from core.repositories import scheduled_posts as repo
from schemas.accounts import AccountCreate
from schemas.scheduled_posts import ScheduledPost, ScheduledStoryOptions

_MEDIA = "a" * 64 + ".jpg"


def _post(account_id: str = "acc", run_at: int | None = None, **overrides: object) -> ScheduledPost:
    created_at, updated = repo.new_post_timestamps()
    when = run_at if run_at is not None else int(time.time()) - 1
    values: dict[str, object] = {
        "post_id": uuid.uuid4().hex,
        "account_id": account_id,
        "kind": "photo",
        "state": "pending",
        "stage": "queued",
        "scheduled_for_unix": when,
        "next_attempt_unix": when,
        "media_names": [_MEDIA],
        "created_at": created_at,
        "updated_unix": updated,
    }
    values.update(overrides)
    return ScheduledPost.model_validate(values)


async def _seed(*posts: ScheduledPost) -> None:
    for account_id in {post.account_id for post in posts}:
        await create_account(AccountCreate(account_id=account_id))
    for post in posts:
        await repo.insert_post(post)


@pytest.mark.asyncio
async def test_insert_round_trips_a_story_with_its_ordered_collage() -> None:
    story = ScheduledStoryOptions(caption="hi", privacy_preset="public", collage_layout="grid")
    names = ["c" * 64 + ".png", "b" * 64 + ".png", "d" * 64 + ".png"]
    post = _post(kind="story", story=story, media_names=names)
    await _seed(post)

    stored = await repo.fetch_post(post.post_id)

    assert stored == post


@pytest.mark.asyncio
async def test_a_keyed_bulk_retry_answers_the_row_it_already_created() -> None:
    first = _post(batch_id="batch", client_key="row-1")
    await _seed(first)

    inserted = await repo.insert_post(_post(batch_id="batch", client_key="row-1"))

    assert inserted is not None
    again, created = inserted

    assert created is False
    assert again.post_id == first.post_id


@pytest.mark.asyncio
async def test_an_insert_for_a_vanished_account_answers_none() -> None:
    assert await repo.insert_post(_post("ghost")) is None


@pytest.mark.asyncio
async def test_claim_takes_the_earliest_due_post_and_one_per_account_at_a_time() -> None:
    now = int(time.time())
    early, late = _post(run_at=now - 20), _post(run_at=now - 10)
    other = _post("other", run_at=now - 5)
    future = _post("third", run_at=now + 3600)
    await _seed(early, late, other, future)

    first = await repo.claim_one(now)
    second = await repo.claim_one(now)
    third = await repo.claim_one(now)

    assert first is not None
    assert first.post_id == early.post_id
    assert second is not None
    assert second.post_id == other.post_id
    assert third is None  # ``late`` waits for its account; ``future`` is not due


@pytest.mark.asyncio
async def test_dispatching_is_the_point_of_no_return_for_a_cancel() -> None:
    post = _post()
    await _seed(post)
    await repo.claim_one(int(time.time()))

    assert await repo.mark_dispatching(post.post_id) is True
    assert await repo.cancel_post(post.post_id, post.account_id) is False
    assert await repo.reschedule_post(post.post_id, post.account_id, int(time.time())) is False
    assert await repo.finish_dispatched(post.post_id, "done", story_id=None) is True
    assert await repo.finish_dispatched(post.post_id, "failed") is False


@pytest.mark.asyncio
async def test_a_cancel_while_queued_beats_the_worker() -> None:
    post = _post()
    await _seed(post)
    await repo.claim_one(int(time.time()))

    assert await repo.cancel_post(post.post_id, post.account_id) is True
    assert await repo.mark_dispatching(post.post_id) is False
    stored = await repo.fetch_post(post.post_id)
    assert stored is not None
    assert stored.state == "cancelled"


@pytest.mark.asyncio
async def test_operator_edits_are_scoped_to_the_owning_account() -> None:
    post = _post()
    await _seed(post, _post("other"))

    assert await repo.cancel_post(post.post_id, "other") is False


@pytest.mark.asyncio
async def test_reschedule_revives_a_failed_post_with_a_clean_slate() -> None:
    post = _post()
    await _seed(post)
    await repo.claim_one(int(time.time()))
    await repo.mark_dispatching(post.post_id)
    await repo.finish_dispatched(post.post_id, "failed", error_code="media_invalid")
    later = int(time.time()) + 600

    assert await repo.reschedule_post(post.post_id, post.account_id, later) is True

    stored = await repo.fetch_post(post.post_id)
    assert stored is not None
    assert (stored.state, stored.stage, stored.attempts, stored.error_code) == (
        "pending",
        "queued",
        0,
        None,
    )
    assert stored.scheduled_for_unix == stored.next_attempt_unix == later


@pytest.mark.asyncio
async def test_restart_recovery_requeues_unsent_and_never_repeats_dispatched() -> None:
    unsent, sent = _post(), _post("other")
    await _seed(unsent, sent)
    now = int(time.time())
    await repo.claim_one(now)
    await repo.claim_one(now)
    await repo.mark_dispatching(sent.post_id)

    requeued, ambiguous = await repo.requeue_interrupted()

    assert requeued == 1
    assert [post.post_id for post in ambiguous] == [sent.post_id]
    after_unsent = await repo.fetch_post(unsent.post_id)
    after_sent = await repo.fetch_post(sent.post_id)
    assert after_unsent is not None
    assert after_unsent.state == "pending"
    assert after_sent is not None
    assert (after_sent.state, after_sent.error_code) == ("ambiguous", "unconfirmed")


@pytest.mark.asyncio
async def test_mark_missed_uses_the_operators_time_not_the_retry_time() -> None:
    now = int(time.time())
    stale = _post(run_at=now - 10_000, next_attempt_unix=now)
    fresh = _post("other", run_at=now - 100)
    await _seed(stale, fresh)

    missed = await repo.mark_missed(now - 3600)

    assert [post.post_id for post in missed] == [stale.post_id]
    assert await repo.next_due_unix() == fresh.next_attempt_unix


@pytest.mark.asyncio
async def test_referenced_media_keeps_everything_but_done_and_cancelled() -> None:
    names = [f"{char * 64}.jpg" for char in "abcd"]
    now = int(time.time())
    posts = [
        _post(f"acc-{index}", run_at=now - 100 + index, media_names=[name])
        for index, name in enumerate(names)
    ]
    await _seed(*posts)
    await repo.cancel_post(posts[0].post_id, posts[0].account_id)
    await repo.claim_one(int(time.time()))  # takes posts[1]
    await repo.mark_dispatching(posts[1].post_id)
    await repo.finish_dispatched(posts[1].post_id, "done")
    await repo.claim_one(int(time.time()))  # takes posts[2]
    await repo.mark_dispatching(posts[2].post_id)
    await repo.finish_dispatched(posts[2].post_id, "failed", error_code="failed")

    assert await repo.referenced_media() == frozenset(names[2:])


@pytest.mark.asyncio
async def test_purge_drops_only_settled_rows_older_than_the_cutoff() -> None:
    kept_open, settled = _post(), _post("other")
    await _seed(kept_open, settled)
    await repo.cancel_post(settled.post_id, settled.account_id)

    removed = await repo.purge_settled_before(int(time.time()) + 10)

    assert removed == 1
    assert await repo.fetch_post(settled.post_id) is None
    assert await repo.fetch_post(kept_open.post_id) is not None


@pytest.mark.asyncio
async def test_deleting_the_account_removes_its_posts() -> None:
    post = _post()
    await _seed(post)

    await delete_account(post.account_id)

    assert await repo.fetch_post(post.post_id) is None
    # Counted directly: ``referenced_media`` joins through the posts, so it would
    # read empty even if the media rows had been left behind.
    with _get_engine().connect() as connection:
        left = connection.exec_driver_sql("SELECT COUNT(*) FROM scheduled_post_media").scalar()
    assert left == 0


@pytest.mark.asyncio
async def test_photo_due_before_sees_only_pending_photos_of_that_account() -> None:
    now = int(time.time())
    await _seed(_post(run_at=now + 60), _post("other", run_at=now + 10))

    assert await repo.photo_due_before("acc", now + 120) is True
    assert await repo.photo_due_before("acc", now + 30) is False
