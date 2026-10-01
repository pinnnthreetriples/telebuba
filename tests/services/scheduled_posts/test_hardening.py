"""Failure paths of the scheduled publisher found by audit.

None of them may block, spin, double-count or lie about what was published.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.exc import OperationalError

from core.config import settings
from core.db import _get_engine, update_account_status
from core.repositories import scheduled_posts as repo
from core.repositories.scheduled_posts import _worker
from schemas.scheduled_posts import (
    ScheduledPostReschedule,
    SchedulePhotoRequest,
    ScheduleStoryRequest,
)
from schemas.telegram_actions import ActionResult
from services import scheduled_posts
from services.accounts import AccountNotFoundError
from services.scheduled_posts import _dispatch, _runtime
from tests.services.scheduled_posts.helpers import (
    in_minutes,
    png_bytes,
    seed_account,
    stored_image,
)

if TYPE_CHECKING:
    from pathlib import Path


async def _due_photo(tmp_path: Path, account_id: str = "acc", color: str = "red") -> str:
    read = await scheduled_posts.schedule_photo(
        account_id,
        SchedulePhotoRequest(media_id=await stored_image(tmp_path, color), run_at=in_minutes(5)),
    )
    return read.post_id


@pytest.mark.asyncio
async def test_an_orphaned_claim_is_recovered_and_never_makes_the_worker_spin(
    tmp_path: Path,
) -> None:
    """An orphan in ``dispatching`` must not make its account's next post unclaimable.

    A settle write that failed leaves it there; reporting the next post as due while
    ``claim_one`` skips the account made the worker loop without ever sleeping.
    """
    await seed_account()
    first = await _due_photo(tmp_path)
    second = await _due_photo(tmp_path, color="blue")
    later = int(time.time()) + 600
    claimed = await repo.claim_one(later)
    assert claimed is not None
    await repo.mark_dispatching(claimed.post_id)

    assert await repo.next_due_unix() is None  # the busy account is skipped, as claim does

    await _runtime._recover()

    orphan = await repo.fetch_post(claimed.post_id)
    assert orphan is not None
    assert (orphan.state, orphan.error_code) == ("ambiguous", "unconfirmed")
    assert await repo.next_due_unix() is not None
    following = await repo.claim_one(later)
    assert following is not None
    assert following.post_id == ({first, second} - {claimed.post_id}).pop()


@pytest.mark.asyncio
async def test_a_worker_step_recovers_an_orphan_and_publishes_past_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await seed_account()
    await _due_photo(tmp_path)
    await _due_photo(tmp_path, color="blue")
    orphan = await repo.claim_one(int(time.time()) + 600)
    assert orphan is not None
    await repo.mark_dispatching(orphan.post_id)
    published: list[str] = []

    async def _execute(account_id: str, _action: object, *, domain: str | None = None) -> object:
        del domain
        published.append(account_id)
        return ActionResult(status="ok", action_type="set_profile_photo", account_id=account_id)

    async def _no_refresh(_account_id: str) -> None:
        return None

    monkeypatch.setattr(_dispatch, "execute", _execute)
    monkeypatch.setattr(_dispatch, "refresh_account_avatar", _no_refresh)
    monkeypatch.setattr(time, "time", lambda: datetime.now(UTC).timestamp() + 600)
    monkeypatch.setattr(_runtime._state, "accepting", True)  # as a started runtime is

    delay = await _runtime._step()

    assert delay == 0.0  # it published something, instead of spinning on nothing
    assert published == ["acc"]


@pytest.mark.asyncio
async def test_a_row_that_no_longer_validates_is_failed_not_retried_forever(
    tmp_path: Path,
) -> None:
    await seed_account()
    await seed_account("other")
    bad = await scheduled_posts.schedule_story(
        "acc",
        ScheduleStoryRequest(media_ids=[await stored_image(tmp_path)], run_at=in_minutes(5)),
    )
    good = await _due_photo(tmp_path, "other", color="blue")
    with _get_engine().begin() as connection:
        connection.exec_driver_sql(
            "UPDATE scheduled_profile_posts SET story_json = ?, next_attempt_unix = 0"
            " WHERE post_id = ?",
            ('{"privacy_preset": "everyone"}', bad.post_id),
        )

    assert await repo.claim_one(int(time.time()) + 600) is None
    following = await repo.claim_one(int(time.time()) + 600)

    assert following is not None
    assert following.post_id == good
    listed = await scheduled_posts.list_account_scheduled("acc")
    assert listed.items == []  # skipped, not a 500
    with _get_engine().connect() as connection:
        state = connection.exec_driver_sql(
            "SELECT state, error_code FROM scheduled_profile_posts WHERE post_id = ?",
            (bad.post_id,),
        ).one()
    assert tuple(state) == ("failed", "scheduled_post_invalid")


@pytest.mark.asyncio
async def test_reviving_a_settled_post_answers_to_the_pending_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await seed_account()
    settled = await _due_photo(tmp_path)
    await scheduled_posts.cancel_scheduled_post("acc", settled)
    await _due_photo(tmp_path, color="blue")
    with _get_engine().begin() as connection:
        connection.exec_driver_sql(
            "UPDATE scheduled_profile_posts SET state = 'failed' WHERE post_id = ?",
            (settled,),
        )
    monkeypatch.setattr(settings.scheduled_posts, "max_pending_per_account", 1)

    with pytest.raises(scheduled_posts.ScheduledPostRefusedError) as caught:
        await scheduled_posts.reschedule_scheduled_post(
            "acc", settled, ScheduledPostReschedule(run_at=in_minutes(30))
        )
    assert caught.value.code == "scheduled_pending_limit"


@pytest.mark.asyncio
async def test_a_post_that_cannot_go_out_never_spends_a_pacing_slot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await seed_account()
    await _due_photo(tmp_path)
    post = await repo.claim_one(int(time.time()) + 600)
    assert post is not None
    await update_account_status("acc", status="frozen")
    slots: list[str] = []

    async def _slot(key: str, _gap: float) -> None:
        slots.append(key)

    monkeypatch.setattr(_dispatch, "await_send_slot", _slot)

    await _dispatch.publish(post, accepting=lambda: True)

    stored = await repo.fetch_post(post.post_id)
    assert stored is not None
    assert (stored.state, stored.error_code) == ("failed", "account_frozen")
    assert slots == []


@pytest.mark.asyncio
async def test_a_settle_write_that_hits_a_locked_database_is_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await seed_account()
    post_id = await _due_photo(tmp_path)
    await repo.claim_one(int(time.time()) + 600)
    failures = [OperationalError("UPDATE", {}, Exception("database is locked"))]

    def _flaky_engine() -> object:
        if failures:
            raise failures.pop()
        return _get_engine()

    monkeypatch.setattr(_worker, "_WRITE_RETRY_SECONDS", 0.0)
    monkeypatch.setattr(_worker, "_get_engine", _flaky_engine)

    assert await repo.mark_dispatching(post_id) is True
    assert failures == []


@pytest.mark.asyncio
async def test_a_keyed_retry_answers_its_post_even_once_it_could_be_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await seed_account()
    request = SchedulePhotoRequest(
        media_id=await stored_image(tmp_path),
        run_at=datetime.now(UTC) + timedelta(seconds=90),
        batch_id="b1",
        client_key="row0",
    )
    first = await scheduled_posts.schedule_photo("acc", request)
    monkeypatch.setattr(settings.scheduled_posts, "max_pending_per_account", 1)
    monkeypatch.setattr(time, "time", lambda: datetime.now(UTC).timestamp() + 600)

    again = await scheduled_posts.schedule_photo("acc", request)

    assert again.post_id == first.post_id


@pytest.mark.asyncio
async def test_an_account_deleted_mid_request_is_a_not_found_not_a_crash(tmp_path: Path) -> None:
    await seed_account()
    media_id = await stored_image(tmp_path)

    async def _vanish(_account_id: str) -> None:
        return None

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("services.scheduled_posts._policy.require_account", _vanish)
        with pytest.raises(AccountNotFoundError):
            await scheduled_posts.schedule_photo(
                "ghost",
                SchedulePhotoRequest(media_id=media_id, run_at=in_minutes(10)),
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("count", "layout", "code"),
    [
        (2, "grid2x2", "scheduled_collage_layout_invalid"),
        (7, None, "scheduled_too_many_images"),
    ],
)
async def test_a_collage_that_could_never_publish_is_refused_up_front(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    count: int,
    layout: str | None,
    code: str,
) -> None:
    await seed_account()
    media_id = await stored_image(tmp_path)
    monkeypatch.setattr(settings.profile_media, "story_collage_max_images", 6)
    request = ScheduleStoryRequest.model_construct(
        media_ids=[media_id] * count,
        run_at=in_minutes(10),
        collage_layout=layout,
        caption=None,
        privacy_preset="contacts",
        protect_content=False,
        period_seconds=86_400,
        filename=None,
        batch_id=None,
        client_key=None,
    )

    with pytest.raises(scheduled_posts.ScheduledPostRefusedError) as caught:
        await scheduled_posts.schedule_story("acc", request)
    assert caught.value.code == code


@pytest.mark.asyncio
async def test_re_uploading_stored_content_never_counts_against_the_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first"
    first.write_bytes(png_bytes("red"))
    stored = await scheduled_posts.store_media(first, "a.png")
    monkeypatch.setattr(settings.scheduled_posts, "max_store_bytes", stored.size_bytes)

    again = await scheduled_posts.store_media(first, "a.png")
    other = tmp_path / "other"
    other.write_bytes(png_bytes("blue"))
    with pytest.raises(scheduled_posts.ScheduledPostRefusedError) as caught:
        await scheduled_posts.store_media(other, "b.png")

    assert again.media_id == stored.media_id
    assert caught.value.code == "scheduled_store_full"
    assert [path.name for path in settings.scheduled_posts.media_dir.iterdir()] == [stored.media_id]


@pytest.mark.asyncio
async def test_a_post_whose_row_vanished_is_not_logged_as_published(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await seed_account()
    await _due_photo(tmp_path)
    post = await repo.claim_one(int(time.time()) + 600)
    assert post is not None
    events: list[str] = []

    async def _execute(account_id: str, _action: object, *, domain: str | None = None) -> object:
        del domain
        return ActionResult(status="ok", action_type="set_profile_photo", account_id=account_id)

    async def _gone(*_args: object, **_kwargs: object) -> bool:
        return False

    async def _log(_level: str, event: str, **_kwargs: object) -> None:
        events.append(event)

    monkeypatch.setattr(_dispatch, "execute", _execute)
    monkeypatch.setattr(_dispatch.repo, "finish_dispatched", _gone)
    monkeypatch.setattr(_dispatch, "log_event", _log)

    await _dispatch.publish(post, accepting=lambda: True)

    assert "account_scheduled_post_published" not in events


@pytest.mark.asyncio
async def test_a_profile_photo_answers_to_its_own_size_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await seed_account()
    media_id = await stored_image(tmp_path)
    monkeypatch.setattr(settings.profile_media, "photo_max_bytes", 10)

    with pytest.raises(scheduled_posts.ScheduledPostRefusedError) as caught:
        await scheduled_posts.schedule_photo(
            "acc", SchedulePhotoRequest(media_id=media_id, run_at=in_minutes(10))
        )
    assert caught.value.code == "scheduled_media_invalid"


@pytest.mark.asyncio
async def test_a_keyed_retry_of_a_row_that_no_longer_validates_is_no_crash(
    tmp_path: Path,
) -> None:
    await seed_account()
    request = ScheduleStoryRequest(
        media_ids=[await stored_image(tmp_path)],
        run_at=in_minutes(10),
        batch_id="b1",
        client_key="row0",
    )
    first = await scheduled_posts.schedule_story("acc", request)
    with _get_engine().begin() as connection:
        connection.exec_driver_sql(
            "UPDATE scheduled_profile_posts SET story_json = ? WHERE post_id = ?",
            ('{"period_seconds": 5}', first.post_id),
        )

    assert await repo.fetch_keyed("b1", "acc", "row0") is None


@pytest.mark.asyncio
async def test_a_keyed_retry_with_a_moved_time_moves_the_post_instead_of_copying_it(
    tmp_path: Path,
) -> None:
    await seed_account()
    media_id = await stored_image(tmp_path)
    first = await scheduled_posts.schedule_photo(
        "acc",
        SchedulePhotoRequest(
            media_id=media_id, run_at=in_minutes(10), batch_id="b1", client_key="row1"
        ),
    )
    later = in_minutes(90)

    again = await scheduled_posts.schedule_photo(
        "acc",
        SchedulePhotoRequest(media_id=media_id, run_at=later, batch_id="b1", client_key="row1"),
    )

    assert again.post_id == first.post_id
    assert int(again.run_at.timestamp()) == int(later.timestamp())
    assert len((await scheduled_posts.list_account_scheduled("acc")).items) == 1


@pytest.mark.asyncio
async def test_a_story_image_answers_to_its_own_size_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await seed_account()
    media_id = await stored_image(tmp_path)
    monkeypatch.setattr(settings.profile_media, "story_image_max_bytes", 10)

    with pytest.raises(scheduled_posts.ScheduledPostRefusedError) as caught:
        await scheduled_posts.schedule_story(
            "acc", ScheduleStoryRequest(media_ids=[media_id], run_at=in_minutes(10))
        )
    assert caught.value.code == "scheduled_media_invalid"
