"""Scheduling policy: everything refusable is refused when the operator schedules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.repositories import scheduled_posts as repo
from schemas.scheduled_posts import (
    ScheduledPostReschedule,
    SchedulePhotoRequest,
    ScheduleStoryRequest,
)
from services import scheduled_posts
from services.accounts import AccountNotFoundError
from services.scheduled_posts import _runtime
from tests.services.scheduled_posts.helpers import (
    in_minutes,
    png_bytes,
    seed_account,
    stored_image,
    stored_video,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable
    from pathlib import Path


async def _refusal(awaitable: Awaitable[object]) -> str:
    with pytest.raises(scheduled_posts.ScheduledPostRefusedError) as caught:
        await awaitable
    return caught.value.code


@pytest.mark.asyncio
async def test_store_media_accepts_an_image_and_names_it_by_content(tmp_path: Path) -> None:
    source = tmp_path / "upload"
    source.write_bytes(png_bytes())

    uploaded = await scheduled_posts.store_media(source, "Holiday.PNG")

    assert uploaded.media_kind == "image"
    assert uploaded.media_id.endswith(".png")
    assert (settings.scheduled_posts.media_dir / uploaded.media_id).is_file()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "content"),
    [("notes.txt", b"hello"), ("fake.jpg", b"not an image"), ("empty.png", b"")],
)
async def test_store_media_refuses_what_could_never_publish(
    tmp_path: Path,
    filename: str,
    content: bytes,
) -> None:
    source = tmp_path / "upload"
    source.write_bytes(content)

    assert await _refusal(scheduled_posts.store_media(source, filename)) == (
        "scheduled_media_invalid"
    )


@pytest.mark.asyncio
async def test_store_media_refuses_once_the_store_is_full(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.scheduled_posts, "max_store_bytes", 10)
    source = tmp_path / "upload"
    source.write_bytes(png_bytes())

    assert await _refusal(scheduled_posts.store_media(source, "a.png")) == "scheduled_store_full"


@pytest.mark.asyncio
async def test_schedule_photo_creates_a_pending_post_and_wakes_the_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await seed_account()
    woken: list[bool] = []
    monkeypatch.setattr(_runtime, "wake", lambda: woken.append(True))
    media_id = await stored_image(tmp_path)
    run_at = in_minutes(10)

    post = await scheduled_posts.schedule_photo(
        "acc",
        SchedulePhotoRequest(media_id=media_id, run_at=run_at, filename="me.png"),
    )

    assert (post.kind, post.state, post.media_kind, post.filename) == (
        "photo",
        "pending",
        "image",
        "me.png",
    )
    assert int(post.run_at.timestamp()) == int(run_at.timestamp())
    assert post.thumb_url == f"/api/v1/accounts/acc/scheduled/{post.post_id}/thumb"
    assert woken == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("minutes", [-5, 0.5, 60 * 24 * 31])
async def test_the_publish_time_must_sit_inside_the_window(tmp_path: Path, minutes: float) -> None:
    await seed_account()
    request = SchedulePhotoRequest(
        media_id=await stored_image(tmp_path), run_at=in_minutes(minutes)
    )

    assert await _refusal(scheduled_posts.schedule_photo("acc", request)) == (
        "scheduled_run_at_out_of_range"
    )


@pytest.mark.asyncio
async def test_scheduling_for_an_unknown_account_is_a_not_found(tmp_path: Path) -> None:
    request = SchedulePhotoRequest(media_id=await stored_image(tmp_path), run_at=in_minutes(10))

    with pytest.raises(AccountNotFoundError):
        await scheduled_posts.schedule_photo("ghost", request)


@pytest.mark.asyncio
async def test_a_media_id_the_store_does_not_hold_is_refused() -> None:
    await seed_account()
    request = SchedulePhotoRequest(media_id="f" * 64 + ".png", run_at=in_minutes(10))

    assert await _refusal(scheduled_posts.schedule_photo("acc", request)) == (
        "scheduled_media_missing"
    )


@pytest.mark.asyncio
async def test_a_video_is_never_a_profile_photo_nor_part_of_a_collage(tmp_path: Path) -> None:
    await seed_account()
    video, image = await stored_video(tmp_path), await stored_image(tmp_path)

    photo = SchedulePhotoRequest(media_id=video, run_at=in_minutes(10))
    collage = ScheduleStoryRequest(media_ids=[image, video], run_at=in_minutes(10))
    video_collage = ScheduleStoryRequest(media_ids=[video, image], run_at=in_minutes(10))

    for attempt in (
        scheduled_posts.schedule_photo("acc", photo),
        scheduled_posts.schedule_story("acc", collage),
        scheduled_posts.schedule_story("acc", video_collage),
    ):
        assert await _refusal(attempt) == "scheduled_media_kind_mismatch"


@pytest.mark.asyncio
async def test_a_story_freezes_its_options_and_a_single_image_drops_the_layout(
    tmp_path: Path,
) -> None:
    await seed_account()
    request = ScheduleStoryRequest(
        media_ids=[await stored_image(tmp_path)],
        run_at=in_minutes(10),
        caption="hello",
        privacy_preset="public",
        collage_layout="grid",
        period_seconds=43_200,
    )

    read = await scheduled_posts.schedule_story("acc", request)

    stored = await repo.fetch_post(read.post_id)
    assert stored is not None
    assert stored.story is not None
    assert (stored.story.caption, stored.story.privacy_preset, stored.story.period_seconds) == (
        "hello",
        "public",
        43_200,
    )
    assert stored.story.collage_layout is None


@pytest.mark.asyncio
async def test_a_video_story_has_no_thumbnail(tmp_path: Path) -> None:
    await seed_account()
    request = ScheduleStoryRequest(media_ids=[await stored_video(tmp_path)], run_at=in_minutes(10))

    read = await scheduled_posts.schedule_story("acc", request)

    assert (read.media_kind, read.thumb_url) == ("video", None)


@pytest.mark.asyncio
async def test_the_per_account_pending_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.scheduled_posts, "max_pending_per_account", 1)
    await seed_account()
    media_id = await stored_image(tmp_path)
    await scheduled_posts.schedule_photo(
        "acc", SchedulePhotoRequest(media_id=media_id, run_at=in_minutes(10))
    )

    second = SchedulePhotoRequest(media_id=media_id, run_at=in_minutes(20))
    assert await _refusal(scheduled_posts.schedule_photo("acc", second)) == (
        "scheduled_pending_limit"
    )


@pytest.mark.asyncio
async def test_a_bulk_retry_with_the_same_key_does_not_double_the_post(tmp_path: Path) -> None:
    await seed_account()
    request = SchedulePhotoRequest(
        media_id=await stored_image(tmp_path),
        run_at=in_minutes(10),
        batch_id="batch-1",
        client_key="row-0",
    )

    first = await scheduled_posts.schedule_photo("acc", request)
    second = await scheduled_posts.schedule_photo("acc", request)

    assert first.post_id == second.post_id
    assert len((await scheduled_posts.list_account_scheduled("acc")).items) == 1


@pytest.mark.asyncio
async def test_reschedule_and_cancel(tmp_path: Path) -> None:
    await seed_account()
    post = await scheduled_posts.schedule_photo(
        "acc",
        SchedulePhotoRequest(media_id=await stored_image(tmp_path), run_at=in_minutes(10)),
    )
    later = datetime.now(UTC) + timedelta(hours=2)

    moved = await scheduled_posts.reschedule_scheduled_post(
        "acc", post.post_id, ScheduledPostReschedule(run_at=later)
    )
    cancelled = await scheduled_posts.cancel_scheduled_post("acc", post.post_id)

    assert int(moved.run_at.timestamp()) == int(later.timestamp())
    assert (cancelled.state, cancelled.thumb_url) == ("cancelled", None)
    with pytest.raises(scheduled_posts.ScheduledPostConflictError):
        await scheduled_posts.cancel_scheduled_post("acc", post.post_id)


@pytest.mark.asyncio
async def test_another_accounts_post_is_not_found(tmp_path: Path) -> None:
    await seed_account()
    await seed_account("other")
    post = await scheduled_posts.schedule_photo(
        "acc",
        SchedulePhotoRequest(media_id=await stored_image(tmp_path), run_at=in_minutes(10)),
    )

    with pytest.raises(scheduled_posts.ScheduledPostNotFoundError):
        await scheduled_posts.cancel_scheduled_post("other", post.post_id)
    assert await scheduled_posts.scheduled_post_thumbnail("other", post.post_id) is None
    assert await scheduled_posts.scheduled_post_thumbnail("acc", post.post_id) is not None
