"""A story the account already has, taken into the campaign's pool as if it were uploaded."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.masstagging_media import read_media
from core.telegram_client import TelegramAccountNotFoundError, TelegramReadError
from schemas.telegram_profile_snapshot import TelegramStoryMedia
from services import masstagging as service
from tests.services.masstagging.fakes import png

if TYPE_CHECKING:
    from tests.services.masstagging.fakes import FakeTelegram

_VIDEO = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32


@pytest.mark.asyncio
async def test_a_photo_story_is_stored_like_an_upload(telegram: FakeTelegram) -> None:
    telegram.story_files[("a1", 7)] = TelegramStoryMedia(content=png(), mime_type="image/jpeg")

    stored = await service.import_story_media("a1", 7)

    assert stored.name == "story-7.jpg"
    assert stored.media_id.endswith(".jpg")
    assert await read_media(stored.media_id) == png()


@pytest.mark.asyncio
async def test_a_video_story_keeps_its_kind(telegram: FakeTelegram) -> None:
    telegram.story_files[("a1", 8)] = TelegramStoryMedia(content=_VIDEO, mime_type="video/mp4")

    stored = await service.import_story_media("a1", 8)

    assert stored.name == "story-8.mp4"
    assert stored.media_id.endswith(".mp4")


@pytest.mark.asyncio
async def test_the_same_story_twice_is_one_file(telegram: FakeTelegram) -> None:
    telegram.story_files[("a1", 7)] = TelegramStoryMedia(content=png())

    first = await service.import_story_media("a1", 7)
    second = await service.import_story_media("a1", 7)

    assert first.media_id == second.media_id


@pytest.mark.asyncio
async def test_a_mime_with_parameters_is_still_read(telegram: FakeTelegram) -> None:
    telegram.story_files[("a1", 9)] = TelegramStoryMedia(
        content=_VIDEO, mime_type="Video/MP4; codecs=avc1"
    )

    assert (await service.import_story_media("a1", 9)).name == "story-9.mp4"


@pytest.mark.asyncio
async def test_a_gone_story_is_unavailable(telegram: FakeTelegram) -> None:
    del telegram

    with pytest.raises(service.MasstaggingInvalidError, match="masstagging_story_unavailable"):
        await service.import_story_media("a1", 404)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["flood_wait", "unavailable", "other"])
async def test_a_refused_read_is_unavailable(telegram: FakeTelegram, kind: str) -> None:
    telegram.story_errors[("a1", 7)] = TelegramReadError("boom", kind=kind)  # ty: ignore[invalid-argument-type]

    with pytest.raises(service.MasstaggingInvalidError, match="masstagging_story_unavailable"):
        await service.import_story_media("a1", 7)


@pytest.mark.asyncio
async def test_an_account_that_is_gone_is_named(telegram: FakeTelegram) -> None:
    telegram.story_errors[("ghost", 7)] = TelegramAccountNotFoundError("ghost")

    with pytest.raises(service.MasstaggingInvalidError, match="account_not_found"):
        await service.import_story_media("ghost", 7)


@pytest.mark.asyncio
async def test_a_file_that_is_not_a_story_kind_is_refused(telegram: FakeTelegram) -> None:
    telegram.story_files[("a1", 7)] = TelegramStoryMedia(content=b"GIF89a", mime_type="image/gif")

    with pytest.raises(service.MasstaggingInvalidError, match="masstagging_media_invalid"):
        await service.import_story_media("a1", 7)


@pytest.mark.asyncio
async def test_a_story_file_that_does_not_decode_is_refused(telegram: FakeTelegram) -> None:
    telegram.story_files[("a1", 7)] = TelegramStoryMedia(
        content=b"not a picture", mime_type="image/jpeg"
    )

    with pytest.raises(service.MasstaggingInvalidError, match="masstagging_media_invalid"):
        await service.import_story_media("a1", 7)


@pytest.mark.asyncio
async def test_a_too_large_story_is_refused(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.profile_media, "story_image_max_bytes", 10)
    telegram.story_files[("a1", 7)] = TelegramStoryMedia(content=png())

    with pytest.raises(service.MasstaggingInvalidError, match="masstagging_media_too_large"):
        await service.import_story_media("a1", 7)
