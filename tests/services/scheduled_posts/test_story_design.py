"""Scheduled stories with a design: stored as scheduled, downgraded at publish time."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from core.config import settings
from core.db import _get_engine
from core.migration_steps_scheduled_posts import _add_scheduled_posts_downgraded
from core.repositories import scheduled_posts as repo
from schemas.scheduled_posts import ScheduledStoryOptions, ScheduleStoryRequest
from schemas.telegram_actions import ActionResult, PostStory
from services import scheduled_posts
from services.scheduled_posts import _dispatch
from services.scheduled_posts._policy import to_read
from tests.services.scheduled_posts.helpers import in_minutes, seed_account, stored_image

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from schemas.scheduled_posts import ScheduledPost

_STICKER = {"url": "example.com", "label": "Shop", "x": 50, "y": 50}
_DESIGN: dict[str, Any] = {
    "caption": "word " * 60,
    "entities": [{"type": "bold", "offset": 0, "length": 4}],
    "period_hours": 48,
    "link_stickers": [_STICKER],
    "pinned": True,
    "protect_content": True,
    "privacy_preset": "close_friends",
}
_REFUSAL = {
    "status": "failed",
    "error_type": "ProfileGatewayError",
    "error_message": "premium_required",
}
_LOST = ["period", "link_stickers", "long_caption", "formatting"]


class _Gateway:
    def __init__(self, *answers: Mapping[str, object]) -> None:
        self.answers = list(answers) or [{}]
        self.stories: list[PostStory] = []

    async def __call__(
        self,
        account_id: str,
        action: PostStory,
        *,
        domain: str | None = None,  # noqa: ARG002 - the real signature
    ) -> ActionResult:
        self.stories.append(action)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        values = {"status": "ok", "action_type": "post_story", **answer}
        return ActionResult(account_id=account_id, **values)  # ty: ignore[invalid-argument-type]


def _install(
    monkeypatch: pytest.MonkeyPatch, *answers: Mapping[str, object], premium: bool | None
) -> _Gateway:
    gateway = _Gateway(*answers)
    monkeypatch.setattr(_dispatch, "execute", gateway)

    async def account(_account_id: str) -> object:
        return SimpleNamespace(status="active", premium=premium)

    monkeypatch.setattr(_dispatch, "fetch_account", account)
    return gateway


async def _claimed(tmp_path: Path, **request: Any) -> ScheduledPost:
    await seed_account()
    media_id = await stored_image(tmp_path)
    await scheduled_posts.schedule_story(
        "acc",
        ScheduleStoryRequest.model_validate(
            {"media_ids": [media_id], "run_at": in_minutes(5), **request}
        ),
    )
    post = await repo.claim_one(int(time.time()) + 600)
    assert post is not None
    return post


async def _publish(post: ScheduledPost) -> ScheduledPost:
    await _dispatch.publish(post, accepting=lambda: True)
    stored = await repo.fetch_post(post.post_id)
    assert stored is not None
    return stored


@pytest.mark.asyncio
async def test_a_design_is_frozen_in_the_row_as_scheduled(tmp_path: Path) -> None:
    await seed_account()
    request = ScheduleStoryRequest.model_validate(
        {"media_ids": [await stored_image(tmp_path)], "run_at": in_minutes(10), "design": _DESIGN}
    )

    read = await scheduled_posts.schedule_story("acc", request)

    stored = await repo.fetch_post(read.post_id)
    assert stored is not None
    assert stored.story is not None
    assert stored.story.period_seconds == 172_800
    assert (stored.story.pinned, stored.story.protect_content) == (True, True)
    assert stored.story.privacy_preset == "close_friends"
    assert [e.type for e in stored.story.entities] == ["bold"]
    assert [s.url for s in stored.story.link_stickers] == ["https://example.com"]
    assert (read.period_hours, read.pinned, read.link_sticker_count) == (48, True, 1)
    assert read.downgraded == []


@pytest.mark.asyncio
async def test_a_design_wins_over_the_flat_fields(tmp_path: Path) -> None:
    await seed_account()
    request = ScheduleStoryRequest.model_validate(
        {
            "media_ids": [await stored_image(tmp_path)],
            "run_at": in_minutes(10),
            "caption": "flat",
            "design": {"caption": "designed", "period_hours": 6},
        }
    )

    read = await scheduled_posts.schedule_story("acc", request)

    assert (read.caption, read.period_hours) == ("designed", 6)


@pytest.mark.asyncio
async def test_a_design_past_the_configured_limits_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.profile_media, "story_link_stickers_max", 1)
    await seed_account()
    request = ScheduleStoryRequest.model_validate(
        {
            "media_ids": [await stored_image(tmp_path)],
            "run_at": in_minutes(10),
            "design": {"link_stickers": [_STICKER, _STICKER]},
        }
    )

    with pytest.raises(scheduled_posts.ScheduledPostRefusedError, match="scheduled_post_invalid"):
        await scheduled_posts.schedule_story("acc", request)


def test_a_row_stored_before_the_design_existed_still_parses() -> None:
    old = json.dumps(
        {
            "media_kind": "image",
            "caption": "old",
            "privacy_preset": "public",
            "protect_content": False,
            "collage_layout": None,
            "period_seconds": 43_200,
        }
    )

    options = ScheduledStoryOptions.model_validate(json.loads(old))

    assert (options.entities, options.link_stickers, options.pinned) == ([], [], False)
    design = options.to_design()
    assert (design.period_hours, design.caption, design.privacy_preset) == (12, "old", "public")


@pytest.mark.asyncio
async def test_a_free_account_publishes_downgraded_and_the_row_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = _install(monkeypatch, {"message_id": 5}, premium=False)
    post = await _claimed(tmp_path, design=_DESIGN)

    stored = await _publish(post)

    assert len(gateway.stories) == 1
    sent = gateway.stories[0]
    assert (sent.period_seconds, sent.link_stickers, sent.entities) == (86_400, [], [])
    assert (sent.pinned, sent.protect_content) == (True, True)
    assert (stored.state, stored.story_id) == ("done", 5)
    assert stored.downgraded == _LOST
    read = to_read(stored)
    assert read.downgraded == _LOST


@pytest.mark.asyncio
async def test_a_premium_account_publishes_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = _install(monkeypatch, {"message_id": 5}, premium=True)
    post = await _claimed(tmp_path, design=_DESIGN)

    stored = await _publish(post)

    sent = gateway.stories[0]
    assert (sent.period_seconds, len(sent.link_stickers), len(sent.entities)) == (172_800, 1, 1)
    assert sent.pinned is True
    assert stored.downgraded == []


@pytest.mark.asyncio
@pytest.mark.parametrize("premium", [None, True])
async def test_a_premium_refusal_retries_downgraded_and_publishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    premium: bool | None,  # noqa: FBT001 - parametrized value
) -> None:
    refusal = {
        "status": "failed",
        "error_type": "ProfileGatewayError",
        "error_message": "premium_required",
    }
    gateway = _install(monkeypatch, refusal, {"message_id": 8}, premium=premium)
    post = await _claimed(tmp_path, design=_DESIGN)

    stored = await _publish(post)

    assert [s.period_seconds for s in gateway.stories] == [172_800, 86_400]
    assert (stored.state, stored.story_id, stored.downgraded) == ("done", 8, _LOST)


@pytest.mark.asyncio
async def test_a_refusal_with_nothing_to_downgrade_fails_the_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    refusal = {
        "status": "failed",
        "error_type": "ProfileGatewayError",
        "error_message": "premium_required",
    }
    gateway = _install(monkeypatch, refusal, premium=None)
    post = await _claimed(tmp_path, caption="plain")

    stored = await _publish(post)

    assert len(gateway.stories) == 1
    assert (stored.state, stored.error_code) == ("failed", "premium_required")
    assert stored.downgraded == []


@pytest.mark.asyncio
async def test_a_story_whose_account_row_is_gone_mid_flight_still_settles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = _install(monkeypatch, {"message_id": 3}, premium=True)
    post = await _claimed(tmp_path, design=_DESIGN)
    calls = 0

    async def account(_account_id: str) -> object | None:
        nonlocal calls
        calls += 1
        # The two pre-checks see the account; the premium lookup does not.
        return SimpleNamespace(status="active", premium=True) if calls <= 2 else None

    monkeypatch.setattr(_dispatch, "fetch_account", account)

    stored = await _publish(post)

    assert stored.state == "done"
    assert gateway.stories[0].period_seconds == 172_800


@pytest.mark.asyncio
async def test_the_downgraded_column_is_migrated_into_an_old_table() -> None:
    with _get_engine().begin() as connection:
        connection.exec_driver_sql("ALTER TABLE scheduled_profile_posts DROP COLUMN downgraded")
        _add_scheduled_posts_downgraded(connection)
        _add_scheduled_posts_downgraded(connection)
        columns = connection.exec_driver_sql("PRAGMA table_info(scheduled_profile_posts)").all()

    assert "downgraded" in {row[1] for row in columns}
