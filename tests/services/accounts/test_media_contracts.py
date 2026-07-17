"""Boundary and partial-failure contracts for account media mutations."""

from __future__ import annotations

from typing import cast

import pytest

from core.config import settings
from schemas.profile_media import AccountProfileMusicUpload, AccountStoryUpload, StoryMediaKind
from schemas.telegram_actions import ActionResult, AddProfileMusic, PostStory
from services.accounts import AccountActionError, add_account_profile_music, post_account_story


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "filename", "limit_name"),
    [
        ("image", "story.PNG", "story_image_max_bytes"),
        ("video", "story.MOV", "story_video_max_bytes"),
    ],
)
async def test_story_upload_accepts_exact_size_limit_and_uses_kind_specific_cap(
    monkeypatch: pytest.MonkeyPatch,
    kind: StoryMediaKind,
    filename: str,
    limit_name: str,
) -> None:
    monkeypatch.setattr(settings.profile_media, "story_image_max_bytes", 3)
    monkeypatch.setattr(settings.profile_media, "story_video_max_bytes", 5)
    captured: list[PostStory] = []

    async def execute(account_id: str, action: object) -> ActionResult:
        assert account_id == "acc-boundary"
        assert isinstance(action, PostStory)
        captured.append(action)
        return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)

    monkeypatch.setattr("services.accounts.media.execute", execute)
    monkeypatch.setattr(
        "services.accounts.media.invalidate_account_profile_cache",
        lambda _id: None,
    )
    monkeypatch.setattr("services.accounts.media.log_event", _noop_log)
    size = getattr(settings.profile_media, limit_name)

    await post_account_story(
        AccountStoryUpload(
            account_id="acc-boundary",
            filename=filename,
            content=b"x" * size,
            media_kind=kind,
        ),
    )

    assert len(captured) == 1
    assert captured[0].media_kind == kind
    assert captured[0].content == b"x" * size


@pytest.mark.asyncio
async def test_story_collage_at_count_limit_forwards_full_operator_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.profile_media, "story_collage_max_images", 3)
    captured: list[PostStory] = []
    log_extras: list[dict[str, object]] = []

    async def execute(account_id: str, action: object) -> ActionResult:
        assert isinstance(action, PostStory)
        captured.append(action)
        return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)

    async def log_event(_level: str, _event: str, **kwargs: object) -> None:
        log_extras.append(cast("dict[str, object]", kwargs["extra"]))

    monkeypatch.setattr("services.accounts.media.execute", execute)
    monkeypatch.setattr(
        "services.accounts.media.invalidate_account_profile_cache",
        lambda _id: None,
    )
    monkeypatch.setattr("services.accounts.media.log_event", log_event)

    await post_account_story(
        AccountStoryUpload(
            account_id="acc-collage",
            filename="story.jpg",
            content=b"primary",
            media_kind="image",
            caption="caption",
            privacy_preset="close_friends",
            period_seconds=21_600,
            protect_content=True,
            extra_images=[b"second", b"third"],
            collage_layout="grid-3",
        ),
    )

    assert len(captured) == 1
    action = captured[0]
    assert (
        action.caption,
        action.privacy_preset,
        action.period_seconds,
        action.protect_content,
        action.extra_images,
        action.collage_layout,
    ) == ("caption", "close_friends", 21_600, True, [b"second", b"third"], "grid-3")
    assert log_extras == [
        {
            "filename": "story.jpg",
            "media_kind": "image",
            "privacy_preset": "close_friends",
            "image_count": 3,
        },
    ]


@pytest.mark.asyncio
async def test_music_upload_preserves_optional_metadata_and_invalidates_before_logging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []

    async def execute(account_id: str, action: object) -> ActionResult:
        events.append(action)
        assert isinstance(action, AddProfileMusic)
        assert (action.filename, action.content, action.title, action.performer) == (
            "track.M4A",
            b"audio",
            "Title",
            "Artist",
        )
        return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)

    def invalidate(account_id: str) -> None:
        events.append(("invalidate", account_id))

    async def log_event(level: str, event: str, **kwargs: object) -> None:
        events.append((level, event, kwargs))

    monkeypatch.setattr("services.accounts.media.execute", execute)
    monkeypatch.setattr("services.accounts.media.invalidate_account_profile_cache", invalidate)
    monkeypatch.setattr("services.accounts.media.log_event", log_event)

    await add_account_profile_music(
        AccountProfileMusicUpload(
            account_id="acc-music",
            filename="track.M4A",
            content=b"audio",
            title="Title",
            performer="Artist",
        ),
    )

    assert isinstance(events[0], AddProfileMusic)
    assert events[1] == ("invalidate", "acc-music")
    assert events[2] == (
        "INFO",
        "account_profile_music_added",
        {
            "account_id": "acc-music",
            "extra": {"filename": "track.M4A", "has_title": True},
        },
    )


@pytest.mark.asyncio
async def test_failed_media_action_invalidates_but_never_emits_success_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    async def execute(account_id: str, action: object) -> ActionResult:
        events.append("execute")
        return ActionResult(
            status="failed",
            action_type=getattr(action, "action_type", "unknown"),
            account_id=account_id,
            error_message="MEDIA_INVALID",
        )

    async def log_event(*_args: object, **_kwargs: object) -> None:
        events.append("log")

    monkeypatch.setattr("services.accounts.media.execute", execute)
    monkeypatch.setattr(
        "services.accounts.media.invalidate_account_profile_cache",
        lambda _id: events.append("invalidate"),
    )
    monkeypatch.setattr("services.accounts.media.log_event", log_event)

    with pytest.raises(AccountActionError, match="MEDIA_INVALID"):
        await add_account_profile_music(
            AccountProfileMusicUpload(
                account_id="acc-failure",
                filename="track.mp3",
                content=b"audio",
            ),
        )

    assert events == ["execute", "invalidate"]


async def _noop_log(*_args: object, **_kwargs: object) -> None:
    return None
