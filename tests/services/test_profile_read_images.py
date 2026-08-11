"""Image response contracts for account profile snapshots and stored avatars."""

from __future__ import annotations

import pytest

from schemas.accounts import AccountProfileSnapshot
from schemas.telegram_profile_snapshot import TelegramProfilePhoto, TelegramStoryThumb
from services.accounts.profile_read import account_avatar_image, account_profile_image


def _image_snapshot() -> AccountProfileSnapshot:
    return AccountProfileSnapshot(
        account_id="acc-1",
        photos=[
            TelegramProfilePhoto(
                photo_id=1,
                access_hash=2,
                file_reference=b"ref",
                thumb_bytes=b"photo-thumb",
            ),
            TelegramProfilePhoto(photo_id=2, access_hash=3, file_reference=b"ref2"),
        ],
        stories=[
            TelegramStoryThumb(
                story_id=9,
                kind="image",
                privacy_preset="contacts",
                thumb_bytes=b"story-thumb",
            ),
        ],
    )


@pytest.mark.asyncio
async def test_account_profile_image_returns_photo_bytes_and_etag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[str] = []

    async def snapshot(
        account_id: str,
        *,
        force_refresh: bool = False,  # noqa: ARG001
    ) -> AccountProfileSnapshot:
        requested.append(account_id)
        return _image_snapshot()

    monkeypatch.setattr("services.accounts.profile_read.fetch_live_account_profile", snapshot)

    image = await account_profile_image("acc-1", kind="photos", item_id=1)

    assert image is not None
    assert requested == ["acc-1"]
    assert image.content == b"photo-thumb"
    assert image.media_type == "image/jpeg"
    assert image.etag


@pytest.mark.asyncio
async def test_account_profile_image_returns_story_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def snapshot(
        account_id: str,
        *,
        force_refresh: bool = False,  # noqa: ARG001
    ) -> AccountProfileSnapshot:
        assert account_id == "acc-1"
        return _image_snapshot()

    monkeypatch.setattr("services.accounts.profile_read.fetch_live_account_profile", snapshot)

    image = await account_profile_image("acc-1", kind="stories", item_id=9)

    assert image is not None
    assert image.content == b"story-thumb"
    assert image.media_type == "image/jpeg"
    assert image.etag


@pytest.mark.asyncio
async def test_account_profile_image_unknown_id_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def snapshot(
        account_id: str,
        *,
        force_refresh: bool = False,  # noqa: ARG001
    ) -> AccountProfileSnapshot:
        assert account_id == "acc-1"
        return _image_snapshot()

    monkeypatch.setattr("services.accounts.profile_read.fetch_live_account_profile", snapshot)

    assert await account_profile_image("acc-1", kind="photos", item_id=999) is None
    assert await account_profile_image("acc-1", kind="stories", item_id=999) is None


@pytest.mark.asyncio
async def test_account_profile_image_no_thumb_bytes_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An existing photo without a cached thumbnail produces no response."""

    async def snapshot(
        account_id: str,
        *,
        force_refresh: bool = False,  # noqa: ARG001
    ) -> AccountProfileSnapshot:
        assert account_id == "acc-1"
        return _image_snapshot()

    monkeypatch.setattr("services.accounts.profile_read.fetch_live_account_profile", snapshot)

    assert await account_profile_image("acc-1", kind="photos", item_id=2) is None


@pytest.mark.asyncio
async def test_account_avatar_image_wraps_db_row(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fetch_avatar(account_id: str) -> tuple[bytes, str] | None:
        assert account_id == "acc-1"
        return b"avatar-bytes", "etag-xyz"

    monkeypatch.setattr("services.accounts.profile_read.fetch_account_avatar", fetch_avatar)

    image = await account_avatar_image("acc-1")

    assert image is not None
    assert image.content == b"avatar-bytes"
    assert image.media_type == "image/jpeg"
    assert image.etag == "etag-xyz"


@pytest.mark.asyncio
async def test_account_avatar_image_none_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fetch_avatar(account_id: str) -> tuple[bytes, str] | None:
        assert account_id == "acc-1"
        return None

    monkeypatch.setattr("services.accounts.profile_read.fetch_account_avatar", fetch_avatar)

    assert await account_avatar_image("acc-1") is None
