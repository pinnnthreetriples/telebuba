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

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from core.telegram_client import create_telegram_client, execute
from core.telegram_client._actions import _typing_seconds
from schemas.device_fingerprint import TelegramClientProfile
from schemas.telegram_actions import (
    AddProfileMusic,
    JoinChannel,
    LeaveChannel,
    PostComment,
    PostStory,
    SendDirectMessage,
    SetProfilePhoto,
    UpdateProfile,
)
from tests.factories import DeviceFingerprintFactory

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
    """Replace ``get_client`` with a coroutine that returns ``client``.

    Action paths now borrow from the per-account pool instead of opening a
    fresh client per call, so tests no longer need to stub the per-call
    ``telegram_client`` context manager.
    """

    async def fake_get_client(_account_id: str) -> object:
        return client

    async def fake_fetch(account_id: str):
        return MagicMock(session_name=account_id)

    monkeypatch.setattr("core.telegram_client._actions.get_client", fake_get_client)
    monkeypatch.setattr("core.telegram_client._actions.fetch_account", fake_fetch)


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
async def test_execute_join_channel_with_plus_hash_dispatches_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> None:
            captured.append(request)

    _patch_client(monkeypatch, FakeClient())

    # The action logic uses extract_invite_hash which parses +HASH correctly.
    result = await execute("acc-2", JoinChannel(channel="+AbC-123_456xYz"))

    assert result.status == "ok"
    assert len(captured) == 1
    req = captured[0]
    assert req.__class__.__name__ == "ImportChatInviteRequest"
    assert getattr(req, "hash", "") == "AbC-123_456xYz"


@pytest.mark.asyncio
async def test_execute_join_channel_with_joinchat_dispatches_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> None:
            captured.append(request)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-3", JoinChannel(channel="joinchat/AbC-123_456xYz"))

    assert result.status == "ok"
    assert len(captured) == 1
    req = captured[0]
    assert req.__class__.__name__ == "ImportChatInviteRequest"
    assert getattr(req, "hash", "") == "AbC-123_456xYz"


@pytest.mark.asyncio
async def test_execute_join_channel_already_participant_returns_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> None:
            raise errors.UserAlreadyParticipantError(request=None)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-already", JoinChannel(channel="@already"))

    assert result.status == "ok"
    assert result.action_type == "join_channel"


@pytest.mark.asyncio
async def test_execute_leave_channel_already_participant_does_not_swallow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> None:
            raise errors.UserAlreadyParticipantError(request=None)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc", LeaveChannel(channel="@hot"))

    assert result.status == "failed"
    assert result.error_type == "UserAlreadyParticipantError"


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
        "core.telegram_client._media.utils.get_input_document",
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
async def test_execute_handles_slow_mode_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> None:
            raise errors.SlowModeWaitError(request=None, capture=30)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-slow", JoinChannel(channel="@hot"))

    assert result.status == "slow_mode_wait"
    assert result.flood_wait_seconds == 30
    assert result.error_type is None


@pytest.mark.asyncio
async def test_execute_handles_premium_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> None:
            raise errors.FloodPremiumWaitError(request=None, capture=9)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-prem", JoinChannel(channel="@hot"))

    assert result.status == "premium_wait"
    assert result.flood_wait_seconds == 9
    assert result.error_type is None


@pytest.mark.asyncio
async def test_execute_handles_peer_flood(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> None:
            raise errors.PeerFloodError(request=None)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-peer", JoinChannel(channel="@hot"))

    assert result.status == "peer_flood"
    assert result.flood_wait_seconds is None
    assert result.error_type is None


def test_create_telegram_client_applies_flood_sleep_threshold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.telegram, "flood_sleep_threshold", 7)
    # Telethon refuses to build a client with an empty api_id/api_hash, so set
    # placeholders — CI has no .env, unlike a local dev machine.
    monkeypatch.setattr(settings.telegram, "api_id", 12345)
    monkeypatch.setattr(settings.telegram, "api_hash", "test-hash")
    profile = TelegramClientProfile(
        account_id="acc",
        session_path=str(tmp_path / "acc"),
        receive_updates=False,
        device=DeviceFingerprintFactory.build(
            account_id="acc",
            platform="linux",
            device_model="PC",
            system_version="Ubuntu 24.04",
            app_version="5.0.0 x64",
        ),
    )
    client = create_telegram_client(profile)
    try:
        assert client.flood_sleep_threshold == 7
    finally:
        if client.session is not None:
            client.session.close()


def test_typing_seconds_scales_and_clamps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "typing_wpm", 45)
    monkeypatch.setattr(settings.warming, "typing_sim_min_seconds", 0.5)
    monkeypatch.setattr(settings.warming, "typing_sim_max_seconds", 12.0)
    assert _typing_seconds("") == 0.5  # clamp to min
    assert _typing_seconds("x" * 20) == pytest.approx(20 * 60 / (5 * 45))
    assert _typing_seconds("x" * 1000) == 12.0  # clamp to max


@pytest.mark.asyncio
async def test_execute_send_dm_simulates_typing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "typing_simulation_enabled", True)
    monkeypatch.setattr(settings.warming, "typing_sim_min_seconds", 0.0)
    monkeypatch.setattr(settings.warming, "typing_sim_max_seconds", 0.0)
    typed = {"flag": False}

    class FakeClient:
        async def connect(self) -> None:
            return None

        def action(self, _entity: object, _action: str) -> object:
            @asynccontextmanager
            async def cm():
                typed["flag"] = True
                yield

            return cm()

        async def send_message(self, user_id: int, text: str) -> object:
            assert user_id == 42
            assert text == "привет"
            return MagicMock(id=555)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc", SendDirectMessage(user_id=42, text="привет"))

    assert result.status == "ok"
    assert result.message_id == 555
    assert typed["flag"] is True


@pytest.mark.asyncio
async def test_execute_send_dm_without_typing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "typing_simulation_enabled", False)

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def send_message(self, _user_id: int, _text: str) -> object:
            return MagicMock(id=7)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc", SendDirectMessage(user_id=42, text="hi"))

    assert result.status == "ok"
    assert result.message_id == 7


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
