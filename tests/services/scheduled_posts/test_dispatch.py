"""Publishing one claimed post: the result mapping, and never twice.

``execute`` is faked on ``services.scheduled_posts._dispatch``, its owning module.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import update_account_status
from core.repositories import scheduled_posts as repo
from core.telegram_client import UNCONFIRMED_ERROR_TYPE
from schemas.scheduled_posts import SchedulePhotoRequest, ScheduleStoryRequest
from schemas.telegram_actions import ActionResult, PostStory, SetProfilePhoto
from services import scheduled_posts
from services.scheduled_posts import _dispatch
from tests.services.scheduled_posts.helpers import in_minutes, seed_account, stored_image

if TYPE_CHECKING:
    from pathlib import Path

    from schemas.scheduled_posts import ScheduledPost


class _Gateway:
    """Records every dispatched action and answers a scripted result."""

    def __init__(self, **result: object) -> None:
        self.result = result
        self.calls: list[tuple[str, object, str | None]] = []
        self.gate: asyncio.Event | None = None
        self.entered = asyncio.Event()

    async def __call__(
        self, account_id: str, action: object, *, domain: str | None = None
    ) -> ActionResult:
        self.calls.append((account_id, action, domain))
        self.entered.set()
        if self.gate is not None:
            await self.gate.wait()
        values = {"status": "ok", "action_type": "post_story", **self.result}
        return ActionResult(account_id=account_id, **values)  # ty: ignore[invalid-argument-type]


@pytest.fixture
def avatar_refreshes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def _refresh(account_id: str) -> None:
        calls.append(account_id)

    monkeypatch.setattr(_dispatch, "refresh_account_avatar", _refresh)
    return calls


def _install(monkeypatch: pytest.MonkeyPatch, **result: object) -> _Gateway:
    gateway = _Gateway(**result)
    monkeypatch.setattr(_dispatch, "execute", gateway)
    return gateway


async def _claimed(tmp_path: Path, kind: str = "photo", **story: object) -> ScheduledPost:
    """Schedule a post, move it into the past, and claim it like the worker does."""
    await seed_account()
    media_id = await stored_image(tmp_path)
    if kind == "photo":
        read = await scheduled_posts.schedule_photo(
            "acc", SchedulePhotoRequest(media_id=media_id, run_at=in_minutes(5))
        )
    else:
        read = await scheduled_posts.schedule_story(
            "acc",
            ScheduleStoryRequest.model_validate(
                {"media_ids": [media_id], "run_at": in_minutes(5), **story}
            ),
        )
    post = await repo.claim_one(int(time.time()) + 600)
    assert post is not None
    assert post.post_id == read.post_id
    return post


async def _publish(post: ScheduledPost) -> ScheduledPost:
    await _dispatch.publish(post, accepting=lambda: True)
    stored = await repo.fetch_post(post.post_id)
    assert stored is not None
    return stored


@pytest.mark.asyncio
async def test_a_photo_publishes_from_the_stored_bytes_under_its_own_domain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    avatar_refreshes: list[str],
) -> None:
    gateway = _install(monkeypatch, action_type="set_profile_photo")
    post = await _claimed(tmp_path)

    stored = await _publish(post)

    assert (stored.state, stored.attempts, stored.error_code) == ("done", 1, None)
    ((account_id, action, domain),) = gateway.calls
    assert (account_id, domain) == ("acc", "scheduled")
    assert isinstance(action, SetProfilePhoto)
    assert action.filename == "photo.png"
    assert avatar_refreshes == ["acc"]


@pytest.mark.asyncio
async def test_a_story_carries_its_frozen_options_and_records_the_story_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = _install(monkeypatch, message_id=77)
    post = await _claimed(tmp_path, "story", caption="hi", privacy_preset="public")

    stored = await _publish(post)

    assert (stored.state, stored.story_id) == ("done", 77)
    action = gateway.calls[0][1]
    assert isinstance(action, PostStory)
    assert (action.caption, action.privacy_preset, action.media_kind) == ("hi", "public", "image")


@pytest.mark.asyncio
async def test_a_lost_answer_after_dispatch_is_ambiguous_and_never_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, status="unavailable", error_type=UNCONFIRMED_ERROR_TYPE)
    post = await _claimed(tmp_path)

    stored = await _publish(post)

    assert (stored.state, stored.error_code) == ("ambiguous", "unconfirmed")
    assert await repo.next_due_unix() is None


@pytest.mark.asyncio
async def test_a_request_that_never_left_the_process_backs_off_and_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, status="unavailable", error_type="TelegramClientPoolError")
    post = await _claimed(tmp_path)

    stored = await _publish(post)

    assert (stored.state, stored.stage, stored.error_code) == ("pending", "queued", "unavailable")
    base = settings.scheduled_posts.retry_base_seconds
    assert stored.next_attempt_unix >= int(time.time()) + base - 2


@pytest.mark.asyncio
async def test_a_flood_wait_retries_after_telegrams_duration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, status="flood_wait", flood_wait_seconds=600)
    post = await _claimed(tmp_path)

    stored = await _publish(post)

    assert (stored.state, stored.error_code) == ("pending", "flood_wait")
    assert stored.next_attempt_unix >= int(time.time()) + 598
    assert stored.scheduled_for_unix == post.scheduled_for_unix  # the operator's time stays


@pytest.mark.asyncio
async def test_a_flood_wait_past_the_grace_window_fails_instead(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, status="flood_wait", flood_wait_seconds=86_400)
    post = await _claimed(tmp_path)

    stored = await _publish(post)

    assert (stored.state, stored.error_code) == ("failed", "flood_wait")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "code"),
    [
        (
            {
                "status": "failed",
                "error_type": "ProfileGatewayError",
                "error_message": "premium_required",
            },
            "premium_required",
        ),
        (
            {"status": "failed", "error_type": "RPCError", "error_message": "raw telethon prose"},
            "failed",
        ),
        ({"status": "peer_flood"}, "peer_flood"),
    ],
)
async def test_every_other_refusal_is_terminal_with_a_bounded_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result: dict[str, object],
    code: str,
) -> None:
    _install(monkeypatch, **result)
    post = await _claimed(tmp_path)

    stored = await _publish(post)

    assert (stored.state, stored.error_code) == ("failed", code)


@pytest.mark.asyncio
async def test_a_frozen_account_fails_without_a_telegram_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = _install(monkeypatch)
    post = await _claimed(tmp_path)
    await update_account_status("acc", status="frozen")

    stored = await _publish(post)

    assert (stored.state, stored.error_code) == ("failed", "account_frozen")
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_a_file_lost_before_dispatch_fails_without_a_telegram_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = _install(monkeypatch)
    post = await _claimed(tmp_path)
    (settings.scheduled_posts.media_dir / post.media_names[0]).unlink()

    stored = await _publish(post)

    assert (stored.state, stored.error_code) == ("failed", "scheduled_media_missing")
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_a_cancel_that_lands_while_the_post_waits_wins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = _install(monkeypatch)
    post = await _claimed(tmp_path)
    await scheduled_posts.cancel_scheduled_post("acc", post.post_id)

    stored = await _publish(post)

    assert stored.state == "cancelled"
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_shutdown_before_dispatch_hands_the_post_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = _install(monkeypatch)
    post = await _claimed(tmp_path)

    await _dispatch.publish(post, accepting=lambda: False)

    stored = await repo.fetch_post(post.post_id)
    assert stored is not None
    assert (stored.state, stored.attempts) == ("pending", 0)
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_cancelled_mid_request_settles_ambiguous(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = _install(monkeypatch)
    gateway.gate = asyncio.Event()
    post = await _claimed(tmp_path)

    task = asyncio.create_task(_dispatch.publish(post, accepting=lambda: True))
    await gateway.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    stored = await repo.fetch_post(post.post_id)
    assert stored is not None
    assert (stored.state, stored.error_code) == ("ambiguous", "unconfirmed")


@pytest.mark.asyncio
async def test_the_avatar_resync_waits_for_a_photo_due_right_after(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    avatar_refreshes: list[str],
) -> None:
    _install(monkeypatch, action_type="set_profile_photo")
    post = await _claimed(tmp_path)
    await scheduled_posts.schedule_photo(
        "acc",
        SchedulePhotoRequest(media_id=post.media_names[0], run_at=in_minutes(2)),
    )

    await _publish(post)

    assert avatar_refreshes == []
