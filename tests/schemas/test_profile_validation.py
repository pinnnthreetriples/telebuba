"""Server-side profile validation — Telegram limits enforced at the schema layer.

The SPA's zod schema is advisory; these constraints are the real guard:
first/last name ≤ 64, bio ≤ 70, username "" (clear) or Telegram-valid
(5-32 chars, letter-first, ``[A-Za-z0-9_]``), and every profile-media
``account_id`` restricted to the shared charset pattern.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from schemas.accounts import AccountProfileUpdateRequest
from schemas.profile_media import (
    AccountProfileMusicRemove,
    AccountProfileMusicUpload,
    AccountProfilePhotoRemove,
    AccountProfilePhotoUpload,
    AccountStoryRemove,
    AccountStoryUpload,
)
from schemas.telegram_actions import PostStory, UpdateProfile

if TYPE_CHECKING:
    from collections.abc import Callable


def _request(**overrides: object) -> AccountProfileUpdateRequest:
    payload: dict[str, object] = {"account_id": "acc-1", "first_name": "Alice"}
    payload.update(overrides)
    return AccountProfileUpdateRequest.model_validate(payload)


@pytest.mark.parametrize(
    "overrides",
    [
        {"first_name": "x" * 64},
        {"last_name": "x" * 64},
        {"bio": "x" * 70},
        {"last_name": ""},  # "" = clear (contract)
        {"bio": ""},
        {"username": ""},  # "" = remove the username (contract)
        {"username": None},  # None = leave unchanged
        {"username": "alice"},  # 5 chars, minimum
        {"username": "Alice_99"},
        {"username": "a" + "b" * 31},  # 32 chars, maximum
    ],
)
def test_profile_update_accepts_valid_payloads(overrides: dict[str, object]) -> None:
    assert _request(**overrides) is not None


@pytest.mark.parametrize(
    "overrides",
    [
        {"first_name": "x" * 65},  # over the 64-char Telegram cap
        {"first_name": ""},  # first name is mandatory on Telegram
        {"last_name": "x" * 65},
        {"bio": "x" * 71},  # over the 70-char about cap
        {"username": "abcd"},  # too short (min 5)
        {"username": "a" + "b" * 32},  # too long (max 32)
        {"username": "1alice"},  # must start with a letter
        {"username": "_alice"},
        {"username": "bad-name"},  # hyphen outside the charset
        {"username": "имя_кир"},  # non-ASCII
    ],
)
def test_profile_update_rejects_invalid_payloads(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _request(**overrides)


def test_update_profile_action_mirrors_the_same_limits() -> None:
    """The gateway action schema enforces the identical constraints.

    Any future caller building ``UpdateProfile`` directly (e.g. warming) hits
    the same wall as the API request model — one shared set of constants.
    """
    assert UpdateProfile(first_name="Alice", username="", last_name="", bio="") is not None
    with pytest.raises(ValidationError):
        UpdateProfile(first_name="Alice", bio="x" * 71)
    with pytest.raises(ValidationError):
        UpdateProfile(first_name="x" * 65)
    with pytest.raises(ValidationError):
        UpdateProfile(first_name="Alice", username="bad-name")


@pytest.mark.parametrize(
    "build",
    [
        lambda account_id: AccountProfilePhotoUpload(
            account_id=account_id, filename="p.jpg", content=b"x"
        ),
        lambda account_id: AccountStoryUpload(
            account_id=account_id, filename="s.jpg", content=b"x", media_kind="image"
        ),
        lambda account_id: AccountProfileMusicUpload(
            account_id=account_id, filename="t.mp3", content=b"x"
        ),
        lambda account_id: AccountProfilePhotoRemove(
            account_id=account_id, photo_id=1, access_hash=2, file_reference=b"x"
        ),
        lambda account_id: AccountStoryRemove(account_id=account_id, story_id=1),
        lambda account_id: AccountProfileMusicRemove(
            account_id=account_id, file_id=1, access_hash=2, file_reference=b"x"
        ),
    ],
)
def test_profile_media_account_id_charset(build: Callable[[str], object]) -> None:
    """All six profile-media models share the account_id charset pattern.

    ``|`` is the dialogue pair_key join character — an account_id carrying it
    would corrupt pair keys downstream, so it must be rejected at the boundary.
    """
    assert build("acc-1.ok_2") is not None
    with pytest.raises(ValidationError):
        build("acc|bad")


def test_post_story_accepts_valid_collage() -> None:
    action = PostStory(
        filename="s.jpg",
        content=b"first",
        media_kind="image",
        extra_images=[b"second"],
        collage_layout="v2",
    )
    assert action.extra_images == [b"second"]
    assert action.collage_layout == "v2"


def test_post_story_single_photo_needs_no_collage_fields() -> None:
    action = PostStory(filename="s.jpg", content=b"x", media_kind="image")
    assert action.extra_images == []
    assert action.collage_layout is None


def test_post_story_rejects_extra_images_on_video() -> None:
    with pytest.raises(ValidationError):
        PostStory(
            filename="s.mp4",
            content=b"vid",
            media_kind="video",
            extra_images=[b"x"],
        )


def test_post_story_rejects_layout_without_extra_images() -> None:
    with pytest.raises(ValidationError):
        PostStory(filename="s.jpg", content=b"x", media_kind="image", collage_layout="v2")


def test_account_story_upload_carries_collage_fields() -> None:
    upload = AccountStoryUpload(
        account_id="acc-1",
        filename="s.jpg",
        content=b"first",
        media_kind="image",
        extra_images=[b"second", b"third"],
        collage_layout="v3",
    )
    assert upload.extra_images == [b"second", b"third"]
    assert upload.collage_layout == "v3"
