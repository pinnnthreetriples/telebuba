"""Profile snapshot, photo, and music read tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from telethon import errors
from telethon.tl.functions.photos import GetUserPhotosRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import (
    DocumentAttributeAudio,
)

from core.config import settings
from core.telegram_client import (
    execute_read,
)
from schemas.telegram_actions import (
    GetUserProfile,
    ListProfileMusic,
    ListProfilePhotos,
)
from schemas.telegram_profile_snapshot import (
    TelegramProfileMusic,
    TelegramProfilePhotos,
    TelegramProfileSnapshot,
)
from tests.core.telegram_client.helpers import patch_read_client as _patch_client


@pytest.mark.asyncio
async def test_get_user_profile_returns_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    requested: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            requested.append(request)
            if isinstance(request, GetFullUserRequest):
                return MagicMock(
                    full_user=MagicMock(about="Hi there", profile_photo=MagicMock(id=900)),
                    users=[
                        MagicMock(
                            first_name="Alice",
                            last_name="Liddell",
                            username="alice",
                            phone="79991234567",
                        ),
                    ],
                )
            return MagicMock()

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-1", GetUserProfile())

    assert isinstance(result, TelegramProfileSnapshot)
    assert result.first_name == "Alice"
    assert result.last_name == "Liddell"
    assert result.username == "alice"
    assert result.phone == "79991234567"
    assert result.bio == "Hi there"
    # The current-avatar id is read authoritatively from UserFull.profile_photo.
    assert result.current_photo_id == 900
    assert any(isinstance(req, GetFullUserRequest) for req in requested)


@pytest.mark.asyncio
async def test_list_profile_music_when_unsupported_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("core.telegram_client._read._MUSIC_API_AVAILABLE", False)
    monkeypatch.setattr("core.telegram_client._read.GetSavedMusicRequest", None)

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:  # pragma: no cover - never called
            msg = "client should not be called when music API absent"
            raise AssertionError(msg)

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-no-music-api", ListProfileMusic())

    assert isinstance(result, TelegramProfileMusic)
    assert result.items == []
    assert result.supported is False


@pytest.mark.asyncio
async def test_list_profile_music_when_supported_maps_audio_attributes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("core.telegram_client._read._MUSIC_API_AVAILABLE", True)

    class FakeMusicRequest:
        def __init__(self, *, id: object, offset: int, limit: int, hash: int) -> None:  # noqa: A002 — mirrors Telethon's TL parameter
            self.id = id
            self.offset = offset
            self.limit = limit
            self.hash = hash

    monkeypatch.setattr("core.telegram_client._read.GetSavedMusicRequest", FakeMusicRequest)

    audio = DocumentAttributeAudio(
        duration=183,
        voice=False,
        title="Memorabilia",
        performer="The Heads",
        waveform=None,
    )
    document = MagicMock(id=555, attributes=[audio])

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, name: str) -> object:
            assert name == "me"
            return MagicMock(name="InputUserSelf")

        async def __call__(self, request: object) -> object:
            assert isinstance(request, FakeMusicRequest)
            assert request.offset == 0
            assert request.hash == 0
            return MagicMock(documents=[document])

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-music", ListProfileMusic())

    assert isinstance(result, TelegramProfileMusic)
    assert result.supported is True
    assert len(result.items) == 1
    track = result.items[0]
    assert track.file_id == 555
    assert track.title == "Memorabilia"
    assert track.performer == "The Heads"
    assert track.duration_seconds == 183


@pytest.mark.asyncio
async def test_list_profile_photos_maps_id_triple_and_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each photo must carry the InputPhoto id triple + a Unix-int date.

    Telethon hands us a ``datetime`` for ``photo.date``; the dispatcher has to
    flatten it to a plain int so the schema stays Pydantic-friendly and the UI
    can format without re-importing datetime.
    """
    photo_a = MagicMock(
        id=111,
        access_hash=22,
        file_reference=b"\x0a",
        date=datetime(2025, 3, 4, 12, 0, tzinfo=UTC),
    )
    photo_b = MagicMock(
        id=222,
        access_hash=33,
        file_reference=b"\x0b",
        date=datetime(2024, 1, 1, tzinfo=UTC),
    )
    photos_payload = MagicMock(photos=[photo_a, photo_b])
    requested: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            requested.append(request)
            return photos_payload

        async def download_media(self, _media: object, *, file: object, thumb: int) -> bytes:  # noqa: ARG002
            assert thumb == -1, "photo grid must fetch the largest preview, not the stripped thumb"
            return b"thumb"

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-photos", ListProfilePhotos(limit=24))

    assert isinstance(result, TelegramProfilePhotos)
    assert any(isinstance(req, GetUserPhotosRequest) for req in requested)
    assert [item.photo_id for item in result.items] == [111, 222]
    assert result.items[0].access_hash == 22
    assert result.items[0].file_reference == b"\x0a"
    assert result.items[0].thumb_bytes == b"thumb"
    assert result.items[0].date_unix == int(
        datetime(2025, 3, 4, 12, 0, tzinfo=UTC).timestamp(),
    )


@pytest.mark.asyncio
async def test_list_profile_photos_empty_account(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:
            return MagicMock(photos=[])

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-no-photos", ListProfilePhotos())

    assert isinstance(result, TelegramProfilePhotos)
    assert result.items == []


@pytest.mark.asyncio
async def test_list_profile_photos_thumb_failure_is_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One bad thumb must not blow up the whole grid.

    Mirrors the story-thumb safety net: ``download_media`` can fail on
    privacy-restricted or cache-evicted photos; the dispatcher swallows
    the RPCError and the card renders without an image instead.
    """
    photo = MagicMock(
        id=1,
        access_hash=2,
        file_reference=b"\x01",
        date=datetime(2024, 1, 1, tzinfo=UTC),
    )

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:
            return MagicMock(photos=[photo])

        async def download_media(self, _media: object, *, file: object, thumb: int) -> bytes:  # noqa: ARG002
            raise errors.RPCError(request=None, message="FILE_REFERENCE_EXPIRED", code=400)

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-bad-thumb", ListProfilePhotos())

    assert isinstance(result, TelegramProfilePhotos)
    assert len(result.items) == 1
    assert result.items[0].photo_id == 1
    assert result.items[0].thumb_bytes is None


def _dated_photo(photo_id: int) -> MagicMock:
    return MagicMock(
        id=photo_id,
        access_hash=photo_id,
        file_reference=b"\x01",
        date=datetime(2024, 1, 1, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_list_profile_photos_flood_wait_breaks_thumb_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first FloodWait stops all remaining thumbnail downloads."""
    monkeypatch.setattr(settings.profile_media, "thumb_concurrency", 1)
    photos = [_dated_photo(n) for n in (1, 2, 3)]
    attempts: list[int] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:
            return MagicMock(photos=photos)

        async def download_media(self, _media: object, *, file: object, thumb: int) -> bytes:  # noqa: ARG002
            attempts.append(1)
            raise errors.FloodWaitError(request=None, capture=33)

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-flood-thumbs", ListProfilePhotos())

    assert isinstance(result, TelegramProfilePhotos)
    assert [item.photo_id for item in result.items] == [1, 2, 3]
    assert all(item.thumb_bytes is None for item in result.items)
    assert len(attempts) == 1, "siblings must skip after the breaker trips"


@pytest.mark.asyncio
async def test_list_profile_photos_thumb_downloads_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Thumbnail concurrency respects the configured bound."""
    monkeypatch.setattr(settings.profile_media, "thumb_concurrency", 2)
    photos = [_dated_photo(n) for n in range(1, 7)]
    live = 0
    peak = 0

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:
            return MagicMock(photos=photos)

        async def download_media(self, _media: object, *, file: object, thumb: int) -> bytes:  # noqa: ARG002
            nonlocal live, peak
            live += 1
            peak = max(peak, live)
            await asyncio.sleep(0)
            live -= 1
            return b"thumb"

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-bounded-thumbs", ListProfilePhotos())

    assert isinstance(result, TelegramProfilePhotos)
    assert all(item.thumb_bytes == b"thumb" for item in result.items)
    assert peak <= 2, f"unbounded thumb fan-out: {peak} concurrent downloads"
