"""Tests for ``core.telegram_client.execute_read`` — read-action dispatcher."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from telethon import errors
from telethon.tl.functions.stories import GetPinnedStoriesRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import (
    DocumentAttributeAudio,
    MessageMediaDocument,
    MessageMediaPhoto,
)

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from core.telegram_client import (
    TelegramAccountNotFoundError,
    TelegramReadError,
    execute_read,
    execute_read_many,
)
from schemas.telegram_actions import (
    GetUserProfile,
    ListPinnedStories,
    ListProfileMusic,
)
from schemas.telegram_profile_snapshot import (
    TelegramPinnedStories,
    TelegramProfileMusic,
    TelegramProfileSnapshot,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.telegram, "session_dir", tmp_path / "sessions")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()
    yield
    reset_logging_for_tests()


def _patch_client(monkeypatch: pytest.MonkeyPatch, client: object) -> None:
    @asynccontextmanager
    async def fake_cm(_request: object):
        yield client

    async def fake_fetch(account_id: str):
        return MagicMock(session_name=account_id)

    monkeypatch.setattr("core.telegram_client._read.telegram_client", fake_cm)
    monkeypatch.setattr("core.telegram_client._read.fetch_account", fake_fetch)


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
                    full_user=MagicMock(about="Hi there"),
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

        async def download_profile_photo(self, target: str, *, file: object) -> bytes:
            assert target == "me"
            assert file is bytes
            return b"jpeg-bytes"

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-1", GetUserProfile())

    assert isinstance(result, TelegramProfileSnapshot)
    assert result.first_name == "Alice"
    assert result.last_name == "Liddell"
    assert result.username == "alice"
    assert result.phone == "79991234567"
    assert result.bio == "Hi there"
    assert result.avatar_bytes == b"jpeg-bytes"
    assert any(isinstance(req, GetFullUserRequest) for req in requested)


@pytest.mark.asyncio
async def test_get_user_profile_handles_missing_avatar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:
            return MagicMock(
                full_user=MagicMock(about=None),
                users=[MagicMock(first_name=None, last_name=None, username=None, phone=None)],
            )

        async def download_profile_photo(self, _target: str, *, file: object) -> object:  # noqa: ARG002
            return None

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-no-photo", GetUserProfile())

    assert isinstance(result, TelegramProfileSnapshot)
    assert result.avatar_bytes is None
    assert result.bio is None


@pytest.mark.asyncio
async def test_list_pinned_stories_returns_items(monkeypatch: pytest.MonkeyPatch) -> None:
    photo_media = MagicMock(spec=MessageMediaPhoto)
    video_doc = MagicMock(mime_type="video/mp4")
    video_media = MagicMock(spec=MessageMediaDocument)
    video_media.document = video_doc
    self_peer = MagicMock(name="InputPeerSelf")

    class FakeStory:
        def __init__(self, story_id: int, media: object, caption: str | None) -> None:
            self.id = story_id
            self.media = media
            self.caption = caption

    stories_payload = MagicMock(
        stories=[
            FakeStory(101, photo_media, "первая"),
            FakeStory(102, video_media, None),
        ],
    )
    requested: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, name: str) -> object:
            assert name == "me"
            return self_peer

        async def __call__(self, request: object) -> object:
            requested.append(request)
            return stories_payload

        async def download_media(self, _media: object, *, file: object, thumb: int) -> bytes:  # noqa: ARG002
            assert thumb == 0
            return b"thumb"

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-stories", ListPinnedStories(limit=5))

    assert isinstance(result, TelegramPinnedStories)
    assert [item.story_id for item in result.items] == [101, 102]
    assert result.items[0].kind == "image"
    assert result.items[0].caption == "первая"
    assert result.items[0].thumb_bytes == b"thumb"
    assert result.items[1].kind == "video"
    assert any(isinstance(req, GetPinnedStoriesRequest) for req in requested)


@pytest.mark.asyncio
async def test_list_pinned_stories_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, _name: str) -> object:
            return MagicMock()

        async def __call__(self, _request: object) -> object:
            return MagicMock(stories=[])

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-empty", ListPinnedStories())

    assert isinstance(result, TelegramPinnedStories)
    assert result.items == []


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
async def test_execute_read_unknown_account_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch(_account_id: str):
        return None

    monkeypatch.setattr("core.telegram_client._read.fetch_account", fake_fetch)

    with pytest.raises(TelegramAccountNotFoundError):
        await execute_read("ghost", GetUserProfile())


@pytest.mark.asyncio
async def test_execute_read_flood_wait_wraps_telethon_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:
            raise errors.FloodWaitError(request=None, capture=42)

        async def download_profile_photo(self, _target: str, *, file: object) -> object:  # noqa: ARG002
            return None

    _patch_client(monkeypatch, FakeClient())

    with pytest.raises(TelegramReadError) as exc_info:
        await execute_read("acc-flood", GetUserProfile())

    assert exc_info.value.reason == "FloodWait(42s)"


@pytest.mark.asyncio
async def test_execute_read_rpc_error_wraps_telethon_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:
            raise errors.RPCError(request=None, message="USER_DEACTIVATED", code=400)

        async def download_profile_photo(self, _target: str, *, file: object) -> object:  # noqa: ARG002
            return None

    _patch_client(monkeypatch, FakeClient())

    with pytest.raises(TelegramReadError) as exc_info:
        await execute_read("acc-rpc", GetUserProfile())

    assert "RPC" in exc_info.value.reason


@pytest.mark.asyncio
async def test_download_story_thumb_swallows_rpc_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    photo_media = MagicMock(spec=MessageMediaPhoto)

    class FakeStory:
        def __init__(self) -> None:
            self.id = 99
            self.media = photo_media
            self.caption = None

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, _name: str) -> object:
            return MagicMock()

        async def __call__(self, _request: object) -> object:
            return MagicMock(stories=[FakeStory()])

        async def download_media(self, _media: object, *, file: object, thumb: int) -> bytes:  # noqa: ARG002
            raise errors.RPCError(request=None, message="MEDIA_INVALID", code=400)

    _patch_client(monkeypatch, FakeClient())

    # A thumb-download RPCError is swallowed inside ``_download_story_thumb``
    # so the story is still listed (with ``thumb_bytes=None``). The outer
    # FloodWait/RPC wrapper triggers only when the *action* fails — keeping
    # one bad thumb from blowing up the whole list view.
    result = await execute_read("acc-stories", ListPinnedStories())

    assert isinstance(result, TelegramPinnedStories)
    assert len(result.items) == 1
    assert result.items[0].story_id == 99
    assert result.items[0].thumb_bytes is None


@pytest.mark.asyncio
async def test_execute_read_many_opens_single_client_for_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: dialog fetch opens exactly ONE Telethon client per batch.

    The previous code did 3 parallel ``execute_read`` calls which opened 3
    Telethon clients on the same ``.session`` SQLite + 3 ``fetch_account``
    queries on ``telebuba.db`` and raced into ``OperationalError: database
    is locked`` under live warming-runtime load.
    """
    opens = 0
    handled: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, _name: str) -> object:
            return MagicMock()

        async def __call__(self, request: object) -> object:
            handled.append(request)
            if isinstance(request, GetFullUserRequest):
                return MagicMock(full_user=MagicMock(about=None), users=[MagicMock()])
            if isinstance(request, GetPinnedStoriesRequest):
                return MagicMock(stories=[])
            # GetSavedMusicRequest fallback
            return MagicMock(documents=[])

        async def download_profile_photo(self, _target: str, *, file: object) -> object:  # noqa: ARG002
            return None

    @asynccontextmanager
    async def fake_cm(_request: object):
        nonlocal opens
        opens += 1
        yield FakeClient()

    async def fake_fetch(account_id: str):
        return MagicMock(session_name=account_id)

    monkeypatch.setattr("core.telegram_client._read.telegram_client", fake_cm)
    monkeypatch.setattr("core.telegram_client._read.fetch_account", fake_fetch)

    results = await execute_read_many(
        "acc-batch",
        [GetUserProfile(), ListPinnedStories(), ListProfileMusic()],
    )

    assert opens == 1, "execute_read_many must open exactly one Telegram client per call"
    assert len(results) == 3, "must return one result per action, in input order"
