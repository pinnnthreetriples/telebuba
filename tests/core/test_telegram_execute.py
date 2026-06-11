"""Tests for ``core.telegram_client.execute`` — the typed-action dispatcher."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from telethon import errors
from telethon.tl.functions.account import (
    SaveMusicRequest,
    UpdateProfileRequest,
    UpdateUsernameRequest,
)
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest
from telethon.tl.functions.photos import UploadProfilePhotoRequest
from telethon.tl.functions.stories import CanSendStoryRequest, SendStoryRequest

from core import telegram_client as telegram_client_module
from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from core.telegram_client import execute
from schemas.telegram_actions import (
    AddProfileMusic,
    JoinChannel,
    LeaveChannel,
    PostComment,
    PostStory,
    SetProfilePhoto,
    UpdateProfile,
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
    """Replace the telegram_client context manager with one that yields ``client``."""

    @asynccontextmanager
    async def fake_cm(_request: object):
        yield client

    monkeypatch.setattr(telegram_client_module, "telegram_client", fake_cm)


@pytest.mark.asyncio
async def test_execute_join_channel_dispatches_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> None:
            captured.append(request)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-1", JoinChannel(channel="@news"))

    assert result.status == "ok"
    assert result.action_type == "join_channel"
    assert result.account_id == "acc-1"
    assert any(isinstance(req, JoinChannelRequest) for req in captured)


@pytest.mark.asyncio
async def test_execute_leave_channel_dispatches_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> None:
            captured.append(request)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-2", LeaveChannel(channel="@news"))

    assert result.status == "ok"
    assert result.action_type == "leave_channel"
    assert any(isinstance(req, LeaveChannelRequest) for req in captured)


@pytest.mark.asyncio
async def test_execute_post_comment_returns_message_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_message = MagicMock(id=4242)

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def send_message(self, chat_id: int, text: str) -> object:
            assert chat_id == 12345
            assert text == "hi"
            return sent_message

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-3", PostComment(chat_id=12345, text="hi"))

    assert result.status == "ok"
    assert result.message_id == 4242
    assert result.action_type == "post_comment"


@pytest.mark.asyncio
async def test_execute_update_profile_dispatches_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> None:
            captured.append(request)

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-4",
        UpdateProfile(first_name="Alice", last_name="L", username="alice", bio="Bio"),
    )

    assert result.status == "ok"
    assert any(isinstance(req, UpdateProfileRequest) for req in captured)
    assert any(isinstance(req, UpdateUsernameRequest) for req in captured)


@pytest.mark.asyncio
async def test_execute_set_profile_photo_uploads_photo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def upload_file(self, _file: object, *, file_name: str) -> object:
            assert file_name == "avatar.jpg"
            return MagicMock()

        async def __call__(self, request: object) -> None:
            captured.append(request)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-photo", SetProfilePhoto(filename="avatar.jpg", content=b"jpg"))

    assert result.status == "ok"
    assert any(isinstance(req, UploadProfilePhotoRequest) for req in captured)


@pytest.mark.asyncio
async def test_execute_post_story_dispatches_story_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, entity: str) -> object:
            assert entity == "me"
            return MagicMock()

        async def upload_file(self, _file: object, *, file_name: str) -> object:
            assert file_name == "story.jpg"
            return MagicMock()

        async def __call__(self, request: object) -> object:
            captured.append(request)
            return MagicMock(id=777)

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-story",
        PostStory(
            filename="story.jpg",
            content=b"jpg",
            media_kind="image",
            caption="hi",
            privacy_preset="contacts",
        ),
    )

    assert result.status == "ok"
    assert result.message_id == 777
    assert any(isinstance(req, CanSendStoryRequest) for req in captured)
    assert any(isinstance(req, SendStoryRequest) for req in captured)


@pytest.mark.asyncio
async def test_execute_add_profile_music_saves_uploaded_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []
    deleted: list[int] = []

    monkeypatch.setattr(
        telegram_client_module.utils,
        "get_input_document",
        lambda _document: MagicMock(),
    )

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def send_file(self, entity: str, _file: object, **_kwargs: object) -> object:
            assert entity == "me"
            return MagicMock(id=99, document=object())

        async def delete_messages(
            self,
            entity: str,
            message_ids: list[int],
            *,
            revoke: bool,
        ) -> None:
            assert entity == "me"
            assert revoke is True
            deleted.extend(message_ids)

        async def __call__(self, request: object) -> None:
            captured.append(request)

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-music",
        AddProfileMusic(filename="track.mp3", content=b"mp3", title="Track"),
    )

    assert result.status == "ok"
    assert deleted == [99]
    assert any(isinstance(req, SaveMusicRequest) for req in captured)


@pytest.mark.asyncio
async def test_execute_handles_flood_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> None:
            raise errors.FloodWaitError(request=None, capture=42)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-5", JoinChannel(channel="@hot"))

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 42
    assert result.error_type is None


@pytest.mark.asyncio
async def test_execute_handles_generic_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> None:
            msg = "boom"
            raise RuntimeError(msg)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-6", JoinChannel(channel="@hot"))

    assert result.status == "failed"
    assert result.error_type == "RuntimeError"
    assert result.error_message == "boom"
