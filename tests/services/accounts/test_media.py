"""Account profile media mutation service tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from core.config import settings
from schemas.profile_media import (
    AccountProfileMusicRemove,
    AccountProfileMusicUpload,
    AccountProfilePhotoRemove,
    AccountProfilePhotoSetMain,
    AccountProfilePhotoUpload,
    AccountStoryPin,
    AccountStoryRemove,
    AccountStoryUpload,
)
from schemas.telegram_actions import (
    ActionResult,
    AddProfileMusic,
    PostStory,
    RemoveProfilePhoto,
    RemoveStory,
    SetMainProfilePhoto,
    SetProfilePhoto,
    ToggleStoryPinned,
)
from services.accounts import (
    AccountActionError,
    add_account_profile_music,
    post_account_story,
    remove_account_profile_music,
    remove_account_profile_photo,
    remove_account_story,
    set_account_main_profile_photo,
    set_account_profile_photo,
    set_account_story_pinned,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


@pytest.mark.asyncio
async def test_set_account_profile_photo_executes_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []

    async def fake_execute(account_id: str, action: object) -> ActionResult:
        captured.append(action)
        return ActionResult(status="ok", action_type="set_profile_photo", account_id=account_id)

    monkeypatch.setattr("services.accounts.media.execute", fake_execute)

    result = await set_account_profile_photo(
        AccountProfilePhotoUpload(
            account_id="account-photo",
            filename="avatar.jpg",
            content=b"jpg",
        ),
    )

    assert result.status == "ok"
    assert isinstance(captured[0], SetProfilePhoto)


@pytest.mark.asyncio
async def test_media_upload_invalidates_profile_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All three media services call ``invalidate_account_profile_cache``.

    Regression: after PR #96 the dialog cached the live snapshot, but only
    ``update_account_profile`` invalidated it on save. Media uploads fell
    through with ``force_refresh=True`` at the UI layer; the new
    optimistic-update flow removed that, so service-level invalidation is
    the only safety net left.
    """
    invalidated: list[str] = []

    monkeypatch.setattr(
        "services.accounts.media.invalidate_account_profile_cache",
        invalidated.append,
    )

    async def fake_execute(account_id: str, action: object) -> ActionResult:
        return ActionResult(
            status="ok",
            action_type=getattr(action, "action_type", "unknown"),
            account_id=account_id,
        )

    monkeypatch.setattr("services.accounts.media.execute", fake_execute)

    await set_account_profile_photo(
        AccountProfilePhotoUpload(
            account_id="acc-photo",
            filename="a.jpg",
            content=b"jpg",
        ),
    )
    await post_account_story(
        AccountStoryUpload(
            account_id="acc-story",
            filename="s.jpg",
            content=b"jpg",
            media_kind="image",
        ),
    )
    await add_account_profile_music(
        AccountProfileMusicUpload(
            account_id="acc-music",
            filename="t.mp3",
            content=b"mp3",
        ),
    )

    assert invalidated == ["acc-photo", "acc-story", "acc-music"]


@pytest.mark.asyncio
async def test_post_account_story_executes_story_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []

    async def fake_execute(account_id: str, action: object) -> ActionResult:
        captured.append(action)
        return ActionResult(status="ok", action_type="post_story", account_id=account_id)

    monkeypatch.setattr("services.accounts.media.execute", fake_execute)

    result = await post_account_story(
        AccountStoryUpload(
            account_id="account-story",
            filename="story.mp4",
            content=b"mp4",
            media_kind="video",
            caption="Story",
            privacy_preset="public",
        ),
    )

    assert result.status == "ok"
    assert isinstance(captured[0], PostStory)
    assert captured[0].privacy_preset == "public"


@pytest.mark.asyncio
async def test_post_account_story_collage_passes_extra_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A collage upload threads image #1 + extra_images + layout into ``PostStory``."""
    captured: list[object] = []

    async def fake_execute(account_id: str, action: object) -> ActionResult:
        captured.append(action)
        return ActionResult(status="ok", action_type="post_story", account_id=account_id)

    monkeypatch.setattr("services.accounts.media.execute", fake_execute)

    result = await post_account_story(
        AccountStoryUpload(
            account_id="acc-collage",
            filename="s.jpg",
            content=b"first",
            media_kind="image",
            extra_images=[b"second", b"third"],
            collage_layout="v3",
        ),
    )

    assert result.status == "ok"
    assert isinstance(captured[0], PostStory)
    assert captured[0].extra_images == [b"second", b"third"]
    assert captured[0].collage_layout == "v3"


@pytest.mark.asyncio
async def test_post_account_story_collage_rejects_too_many_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.profile_media, "story_collage_max_images", 3)

    async def fake_execute(account_id: str, action: object) -> ActionResult:  # noqa: ARG001
        msg = "execute should not be reached"
        raise AssertionError(msg)

    monkeypatch.setattr("services.accounts.media.execute", fake_execute)

    with pytest.raises(AccountActionError) as excinfo:
        await post_account_story(
            AccountStoryUpload(
                account_id="acc-collage",
                filename="s.jpg",
                content=b"first",
                media_kind="image",
                extra_images=[b"a", b"b", b"c"],  # total 4 > cap 3
            ),
        )
    assert str(excinfo.value) == "story_collage_too_many_images"


@pytest.mark.asyncio
async def test_post_account_story_rejects_extra_images_on_video(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_execute(account_id: str, action: object) -> ActionResult:  # noqa: ARG001
        msg = "execute should not be reached"
        raise AssertionError(msg)

    monkeypatch.setattr("services.accounts.media.execute", fake_execute)

    with pytest.raises(AccountActionError) as excinfo:
        await post_account_story(
            AccountStoryUpload(
                account_id="acc-collage",
                filename="s.mp4",
                content=b"vid",
                media_kind="video",
                extra_images=[b"x"],
            ),
        )
    assert str(excinfo.value) == "story_collage_requires_image"


@pytest.mark.asyncio
async def test_post_account_story_collage_rejects_oversize_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.profile_media, "story_image_max_bytes", 3)

    async def fake_execute(account_id: str, action: object) -> ActionResult:  # noqa: ARG001
        msg = "execute should not be reached"
        raise AssertionError(msg)

    monkeypatch.setattr("services.accounts.media.execute", fake_execute)

    with pytest.raises(ValueError, match="too large"):
        await post_account_story(
            AccountStoryUpload(
                account_id="acc-collage",
                filename="s.jpg",
                content=b"ok",  # 2 bytes, under the 3-byte cap
                media_kind="image",
                extra_images=[b"way-too-big"],
            ),
        )


@pytest.mark.asyncio
async def test_post_account_story_collage_rejects_bad_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_execute(account_id: str, action: object) -> ActionResult:  # noqa: ARG001
        msg = "execute should not be reached"
        raise AssertionError(msg)

    monkeypatch.setattr("services.accounts.media.execute", fake_execute)

    with pytest.raises(ValueError, match="must be one of"):
        await post_account_story(
            AccountStoryUpload(
                account_id="acc-collage",
                filename="s.txt",  # not an image suffix
                content=b"first",
                media_kind="image",
                extra_images=[b"second"],
            ),
        )


@pytest.mark.asyncio
async def test_add_account_profile_music_executes_music_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []

    async def fake_execute(account_id: str, action: object) -> ActionResult:
        captured.append(action)
        return ActionResult(status="ok", action_type="add_profile_music", account_id=account_id)

    monkeypatch.setattr("services.accounts.media.execute", fake_execute)

    result = await add_account_profile_music(
        AccountProfileMusicUpload(
            account_id="account-music",
            filename="track.mp3",
            content=b"mp3",
            title="Track",
        ),
    )

    assert result.status == "ok"
    assert isinstance(captured[0], AddProfileMusic)
    assert captured[0].title == "Track"


@pytest.mark.asyncio
async def test_remove_account_profile_photo_executes_action_and_invalidates_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The remove-photo service must reach Telegram with the InputPhoto id triple.

    Mirrors the music-removal contract: passing only ``photo_id`` is not enough
    — ``access_hash`` and ``file_reference`` are required for Telethon's
    ``DeletePhotosRequest``, and the in-process snapshot cache has to be
    cleared so the next dialog open shows the new photo set.
    """
    captured: list[object] = []
    invalidated: list[str] = []

    async def fake_execute(account_id: str, action: object) -> ActionResult:
        captured.append(action)
        return ActionResult(status="ok", action_type="remove_profile_photo", account_id=account_id)

    monkeypatch.setattr("services.accounts.media.execute", fake_execute)
    monkeypatch.setattr(
        "services.accounts.media.invalidate_account_profile_cache",
        invalidated.append,
    )

    result = await remove_account_profile_photo(
        AccountProfilePhotoRemove(
            account_id="account-photo-remove",
            photo_id=4242,
            access_hash=7,
            file_reference=b"\x01\x02",
        ),
    )

    assert result.status == "ok"
    assert isinstance(captured[0], RemoveProfilePhoto)
    assert captured[0].photo_id == 4242
    assert captured[0].access_hash == 7
    assert captured[0].file_reference == b"\x01\x02"
    assert invalidated == ["account-photo-remove"]


@pytest.mark.asyncio
async def test_set_account_main_profile_photo_executes_action_and_invalidates_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """«Сделать основным» must reach Telegram with the InputPhoto triple + clear cache."""
    captured: list[object] = []
    invalidated: list[str] = []

    async def fake_execute(account_id: str, action: object) -> ActionResult:
        captured.append(action)
        return ActionResult(
            status="ok", action_type="set_main_profile_photo", account_id=account_id
        )

    monkeypatch.setattr("services.accounts.media.execute", fake_execute)
    monkeypatch.setattr(
        "services.accounts.media.invalidate_account_profile_cache",
        invalidated.append,
    )

    result = await set_account_main_profile_photo(
        AccountProfilePhotoSetMain(
            account_id="account-photo-main",
            photo_id=4242,
            access_hash=7,
            file_reference=b"\x01\x02",
        ),
    )

    assert result.status == "ok"
    assert isinstance(captured[0], SetMainProfilePhoto)
    assert captured[0].photo_id == 4242
    assert captured[0].access_hash == 7
    assert captured[0].file_reference == b"\x01\x02"
    assert invalidated == ["account-photo-main"]


@pytest.mark.asyncio
async def test_set_account_main_profile_photo_invalidates_cache_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A FAILED «Сделать основным» must still drop the cached profile snapshot.

    Regression (debug.log 2026-07-13 18:11:39): a failed promote kept the stale
    snapshot alive, so the dialog kept offering photo ids that no longer existed
    on the server and the operator re-clicked dead entries. Invalidate whether
    the action succeeded or not — the next open re-reads live state.
    """
    invalidated: list[str] = []

    async def fake_execute(account_id: str, _action: object) -> ActionResult:
        return ActionResult(
            status="failed",
            action_type="set_main_profile_photo",
            account_id=account_id,
            error_type="RuntimeError",
            error_message="Target profile photo is no longer in the account's history",
        )

    monkeypatch.setattr("services.accounts.media.execute", fake_execute)
    monkeypatch.setattr(
        "services.accounts.media.invalidate_account_profile_cache",
        invalidated.append,
    )

    with pytest.raises(AccountActionError):
        await set_account_main_profile_photo(
            AccountProfilePhotoSetMain(
                account_id="account-photo-main-failed",
                photo_id=4242,
                access_hash=7,
                file_reference=b"\x01\x02",
            ),
        )

    assert invalidated == ["account-photo-main-failed"]


def _media_failure_calls() -> list[tuple[object, object]]:
    """(service coroutine, payload) for every media mutation — used twice below."""
    return [
        (
            set_account_profile_photo,
            AccountProfilePhotoUpload(account_id="acc-inv", filename="a.jpg", content=b"jpg"),
        ),
        (
            post_account_story,
            AccountStoryUpload(
                account_id="acc-inv", filename="s.jpg", content=b"jpg", media_kind="image"
            ),
        ),
        (
            add_account_profile_music,
            AccountProfileMusicUpload(account_id="acc-inv", filename="t.mp3", content=b"mp3"),
        ),
        (
            remove_account_profile_music,
            AccountProfileMusicRemove(
                account_id="acc-inv", file_id=1, access_hash=2, file_reference=b"\x01"
            ),
        ),
        (
            remove_account_profile_photo,
            AccountProfilePhotoRemove(
                account_id="acc-inv", photo_id=1, access_hash=2, file_reference=b"\x01"
            ),
        ),
        (remove_account_story, AccountStoryRemove(account_id="acc-inv", story_id=9)),
        (set_account_story_pinned, AccountStoryPin(account_id="acc-inv", story_id=9, pinned=True)),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service_call", "payload"),
    _media_failure_calls(),
    ids=lambda value: getattr(value, "__name__", None),
)
async def test_every_media_mutation_invalidates_cache_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    service_call: Callable[[Any], Awaitable[ActionResult]],
    payload: Any,
) -> None:
    """EVERY media mutation must invalidate the snapshot cache on failure too.

    Regression: only set-main applied the #249 invalidate-before-raise pattern;
    the other mutations raised first, kept the stale snapshot for the TTL, and
    the modal went on offering ids the server no longer recognised.
    """
    invalidated: list[str] = []

    async def fake_execute(account_id: str, action: object) -> ActionResult:
        return ActionResult(
            status="failed",
            action_type=getattr(action, "action_type", "unknown"),
            account_id=account_id,
            error_type="RuntimeError",
            error_message="boom",
        )

    monkeypatch.setattr("services.accounts.media.execute", fake_execute)
    monkeypatch.setattr(
        "services.accounts.media.invalidate_account_profile_cache",
        invalidated.append,
    )

    with pytest.raises(AccountActionError):
        await service_call(payload)

    assert invalidated == ["acc-inv"], "failed mutations must still drop the cached snapshot"


@pytest.mark.asyncio
async def test_media_mutation_unavailable_maps_to_stable_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ``unavailable`` gateway result raises the stable ``unavailable`` code.

    ``raise_for_result`` must NOT leak the raw pool-error message as the code —
    the API maps ``unavailable`` to 503 (infrastructure), not 400 (client).
    """

    async def fake_execute(account_id: str, action: object) -> ActionResult:
        return ActionResult(
            status="unavailable",
            action_type=getattr(action, "action_type", "unknown"),
            account_id=account_id,
            error_type="TelegramClientPoolError",
            error_message="telegram pool connect failed for acc-inv: boom",
        )

    monkeypatch.setattr("services.accounts.media.execute", fake_execute)
    monkeypatch.setattr(
        "services.accounts.media.invalidate_account_profile_cache",
        lambda _account_id: None,
    )

    with pytest.raises(AccountActionError) as excinfo:
        await remove_account_story(AccountStoryRemove(account_id="acc-inv", story_id=9))

    assert excinfo.value.code == "unavailable"


@pytest.mark.asyncio
async def test_remove_account_story_executes_action_and_invalidates_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Service must reach Telegram with ``RemoveStory`` + clear the profile cache.

    Telegram's deleteStories doesn't error on unknown IDs (drops them
    silently), so the only signals we test are the action shape and the
    cache invalidation — both of which the optimistic UI relies on.
    """
    captured: list[object] = []
    invalidated: list[str] = []

    async def fake_execute(account_id: str, action: object) -> ActionResult:
        captured.append(action)
        return ActionResult(status="ok", action_type="remove_story", account_id=account_id)

    monkeypatch.setattr("services.accounts.media.execute", fake_execute)
    monkeypatch.setattr(
        "services.accounts.media.invalidate_account_profile_cache",
        invalidated.append,
    )

    result = await remove_account_story(
        AccountStoryRemove(account_id="account-story-remove", story_id=9876),
    )

    assert result.status == "ok"
    assert isinstance(captured[0], RemoveStory)
    assert captured[0].story_id == 9876
    assert invalidated == ["account-story-remove"]


@pytest.mark.asyncio
@pytest.mark.parametrize("pinned", [True, False])
async def test_set_account_story_pinned_executes_action_and_invalidates_cache(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pinned: bool,
) -> None:
    """Pinning/unpinning reaches Telegram with the target state + clears the cache."""
    captured: list[object] = []
    invalidated: list[str] = []

    async def fake_execute(account_id: str, action: object) -> ActionResult:
        captured.append(action)
        return ActionResult(status="ok", action_type="toggle_story_pinned", account_id=account_id)

    monkeypatch.setattr("services.accounts.media.execute", fake_execute)
    monkeypatch.setattr(
        "services.accounts.media.invalidate_account_profile_cache",
        invalidated.append,
    )

    result = await set_account_story_pinned(
        AccountStoryPin(account_id="account-story-pin", story_id=3210, pinned=pinned),
    )

    assert result.status == "ok"
    assert isinstance(captured[0], ToggleStoryPinned)
    assert captured[0].story_id == 3210
    assert captured[0].pinned is pinned
    assert invalidated == ["account-story-pin"]


@pytest.mark.asyncio
async def test_set_account_story_pinned_raises_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Telegram refusal surfaces as ``AccountActionError`` (mapped to the envelope)."""

    async def fake_execute(account_id: str, _action: object) -> ActionResult:
        return ActionResult(
            status="failed",
            action_type="toggle_story_pinned",
            account_id=account_id,
            error_type="RPCError",
            error_message="STORY_ID_INVALID",
        )

    monkeypatch.setattr("services.accounts.media.execute", fake_execute)

    with pytest.raises(AccountActionError):
        await set_account_story_pinned(
            AccountStoryPin(account_id="account-story-pin", story_id=1, pinned=True),
        )


@pytest.mark.asyncio
async def test_remove_account_profile_photo_raises_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telegram refusals surface as ``ValueError`` — the UI shows the message inline."""

    async def fake_execute(account_id: str, _action: object) -> ActionResult:
        return ActionResult(
            status="failed",
            action_type="remove_profile_photo",
            account_id=account_id,
            error_type="RPCError",
            error_message="PHOTO_INVALID",
        )

    monkeypatch.setattr("services.accounts.media.execute", fake_execute)

    with pytest.raises(ValueError, match="PHOTO_INVALID"):
        await remove_account_profile_photo(
            AccountProfilePhotoRemove(
                account_id="acc",
                photo_id=1,
                access_hash=2,
                file_reference=b"\x03",
            ),
        )


@pytest.mark.asyncio
async def test_profile_media_rejects_wrong_extension() -> None:
    with pytest.raises(ValueError, match="profile photo must be one of"):
        await set_account_profile_photo(
            AccountProfilePhotoUpload(
                account_id="account-photo",
                filename="avatar.gif",
                content=b"gif",
            ),
        )
