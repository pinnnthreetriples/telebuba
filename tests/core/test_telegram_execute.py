"""Tests for ``core.telegram_client.execute`` — the typed-action dispatcher."""

from __future__ import annotations

from contextlib import asynccontextmanager
from io import BytesIO
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from PIL import Image
from telethon import errors
from telethon.tl.functions.account import (
    SaveMusicRequest,
    UpdateProfileRequest,
    UpdateUsernameRequest,
)
from telethon.tl.functions.channels import (
    GetFullChannelRequest,
    JoinChannelRequest,
    LeaveChannelRequest,
)
from telethon.tl.functions.photos import DeletePhotosRequest, UploadProfilePhotoRequest
from telethon.tl.functions.stories import (
    CanSendStoryRequest,
    DeleteStoriesRequest,
    SendStoryRequest,
)
from telethon.tl.types import InputPhoto

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from core.telegram_client import create_telegram_client, execute
from core.telegram_client._actions import _typing_seconds
from schemas.device_fingerprint import TelegramClientProfile
from schemas.telegram_actions import (
    AddProfileMusic,
    ClickButton,
    CommentOnPost,
    JoinChannel,
    JoinDiscussionGroup,
    LeaveChannel,
    PostComment,
    PostStory,
    RemoveProfilePhoto,
    RemoveStory,
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
async def test_execute_comment_on_post_returns_message_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_message = MagicMock(id=8181)

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def send_message(self, entity: str, text: str, *, comment_to: int) -> object:
            assert entity == "@news"
            assert text == "great post"
            assert comment_to == 55
            return sent_message

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-comment",
        CommentOnPost(channel="@news", post_id=55, text="great post"),
    )

    assert result.status == "ok"
    assert result.action_type == "comment_on_post"
    assert result.message_id == 8181


@pytest.mark.asyncio
async def test_execute_comment_on_post_handles_flood_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def send_message(self, _entity: str, _text: str, *, comment_to: int) -> object:  # noqa: ARG002
            raise errors.FloodWaitError(request=None, capture=17)

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-comment-flood",
        CommentOnPost(channel="@news", post_id=55, text="hi"),
    )

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 17


@pytest.mark.asyncio
async def test_execute_comment_on_post_write_forbidden_surfaces_error_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A domain error must surface its exception class name for #117 to branch on."""

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def send_message(self, _entity: str, _text: str, *, comment_to: int) -> object:  # noqa: ARG002
            raise errors.ChatWriteForbiddenError(request=None)

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-comment-forbidden",
        CommentOnPost(channel="@news", post_id=55, text="hi"),
    )

    assert result.status == "failed"
    assert result.error_type == "ChatWriteForbiddenError"


@pytest.mark.asyncio
async def test_execute_click_button_clicks_by_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clicked: list[object] = []
    message = MagicMock()

    async def fake_click(i: object = None, *, text: object = None) -> object:
        clicked.append((i, text))
        return MagicMock()

    message.click = fake_click

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_messages(self, chat_id: int, *, ids: int) -> object:
            assert chat_id == 123
            assert ids == 456
            return message

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-click",
        ClickButton(chat_id=123, message_id=456, button_index=2),
    )

    assert result.status == "ok"
    assert result.action_type == "click_button"
    assert clicked == [(2, None)]


@pytest.mark.asyncio
async def test_execute_click_button_clicks_by_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clicked: list[object] = []
    message = MagicMock()

    async def fake_click(i: object = None, *, text: object = None) -> object:
        clicked.append((i, text))
        return MagicMock()

    message.click = fake_click

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_messages(self, _chat_id: int, *, ids: int) -> object:  # noqa: ARG002
            return message

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-click-text",
        ClickButton(chat_id=123, message_id=456, button_text="I am not a robot"),
    )

    assert result.status == "ok"
    assert clicked == [(None, "I am not a robot")]


@pytest.mark.asyncio
async def test_execute_click_button_defaults_to_first_button(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clicked: list[object] = []
    message = MagicMock()

    async def fake_click(i: object = None, *, text: object = None) -> object:
        clicked.append((i, text))
        return MagicMock()

    message.click = fake_click

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_messages(self, _chat_id: int, *, ids: int) -> object:  # noqa: ARG002
            return message

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-click-default", ClickButton(chat_id=1, message_id=2))

    assert result.status == "ok"
    assert clicked == [(0, None)]


@pytest.mark.asyncio
async def test_execute_click_button_no_message_is_noop_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the message is gone there is nothing to click — succeed as a no-op."""

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_messages(self, _chat_id: int, *, ids: int) -> object:  # noqa: ARG002
            return None

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-click-missing", ClickButton(chat_id=1, message_id=2))

    assert result.status == "ok"
    assert result.message_id is None


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
    uploaded_bytes: list[bytes] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, entity: str) -> object:
            assert entity == "me"
            return MagicMock()

        async def upload_file(self, file: BytesIO, *, file_name: str) -> object:
            assert file_name == "story.jpg"
            # The story dispatcher normalises images before upload — capture
            # what actually reaches Telegram so we can assert the resize hit.
            uploaded_bytes.append(file.read())
            return MagicMock()

        async def __call__(self, request: object) -> object:
            captured.append(request)
            return MagicMock(id=777)

    _patch_client(monkeypatch, FakeClient())

    # A 4:5 portrait JPEG — Telegram would reject this aspect ratio
    # server-side; the normalisation step has to letterbox it to 1080x1920.
    buffer = BytesIO()
    Image.new("RGB", (400, 500), (255, 0, 0)).save(buffer, format="JPEG")

    result = await execute(
        "acc-story",
        PostStory(
            filename="story.jpg",
            content=buffer.getvalue(),
            media_kind="image",
            caption="hi",
            privacy_preset="contacts",
        ),
    )

    assert result.status == "ok"
    assert result.message_id == 777
    assert any(isinstance(req, CanSendStoryRequest) for req in captured)
    assert any(isinstance(req, SendStoryRequest) for req in captured)
    # Regression guard: the photo that actually hit upload_file must already
    # be normalised to Telegram's 1080x1920 story canvas — otherwise the
    # server rejects with PHOTO_INVALID_DIMENSIONS.
    with Image.open(BytesIO(uploaded_bytes[0])) as sent:
        assert sent.size == (1080, 1920)


def test_normalize_story_image_renders_blurred_background_canvas() -> None:
    """The story canvas must hit 1080x1920 JPEG with a blurred fill, not black bars.

    Matches the official Telegram Android client's story composition
    (StoryEntry.java: ``backgroundFile`` is a blurred upscale of the source).
    Two regression guards:

    - Top-left corner of a coloured-source upload must NOT be pure black —
      otherwise we slipped back to the old solid-letterbox path.
    - Centre pixel reads back close to the source colour because the fitted
      copy sits on top of the blurred background.
    """
    from core.telegram_client._media import (  # noqa: PLC0415 — internal helper
        _normalize_story_image_for_telegram,
    )

    source_rgb = (200, 50, 30)
    wide = BytesIO()
    Image.new("RGB", (800, 600), source_rgb).save(wide, format="JPEG")
    out = _normalize_story_image_for_telegram(wide.getvalue())

    with Image.open(BytesIO(out)) as result:
        assert result.size == (1080, 1920)
        assert result.mode == "RGB"
        assert result.format == "JPEG"
        # ``Image.getpixel`` returns ``float | tuple[int, ...] | None`` per
        # Pillow's stub union; the convert("RGB") guarantees a 3-tuple
        # at runtime, but ty needs the narrowing assertion to drop the union.
        corner = result.convert("RGB").getpixel((10, 10))
        assert isinstance(corner, tuple)
        assert corner != (0, 0, 0), "story background must be blurred fill, not black"
        centre = result.convert("RGB").getpixel((540, 960))
        assert isinstance(centre, tuple)
        # Source is solid red — both blurred background and fitted copy
        # should sit close to the source colour everywhere.
        assert abs(int(centre[0]) - source_rgb[0]) < 30, "fitted source must dominate the centre"


def test_normalize_story_image_rejects_non_image_bytes() -> None:
    from core.telegram_client._media import (  # noqa: PLC0415 — internal helper
        _normalize_story_image_for_telegram,
    )

    with pytest.raises(ValueError, match="JPG/PNG/WebP"):
        _normalize_story_image_for_telegram(b"not an image")


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
async def test_execute_remove_profile_photo_sends_delete_photos_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Removing one photo must hit ``DeletePhotosRequest`` with the InputPhoto triple.

    Telegram auto-promotes the next photo to current — we don't re-set the
    avatar from the gateway; the optimistic UI mirrors that promotion locally
    and the next ↻ refresh re-syncs.
    """
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> None:
            captured.append(request)

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-photo-remove",
        RemoveProfilePhoto(
            photo_id=4242,
            access_hash=7,
            file_reference=b"\x01\x02",
        ),
    )

    assert result.status == "ok"
    delete_requests = [req for req in captured if isinstance(req, DeletePhotosRequest)]
    assert len(delete_requests) == 1
    input_photos = delete_requests[0].id
    assert len(input_photos) == 1
    sent = input_photos[0]
    # ``DeletePhotosRequest.id`` is typed as ``InputPhoto | InputPhotoEmpty``;
    # narrow with an isinstance so ty knows the access_hash / file_reference
    # attributes are present.
    assert isinstance(sent, InputPhoto)
    assert sent.id == 4242
    assert sent.access_hash == 7
    assert sent.file_reference == b"\x01\x02"


@pytest.mark.asyncio
async def test_execute_remove_story_sends_delete_stories_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One ``stories.deleteStories`` call covers both active and pinned removal.

    The endpoint takes a batch ``Vector<int>``; we still pass a single id
    because the UI deletes one slide at a time, but the call signature must
    be a list so the server doesn't reject the request as malformed. Bad
    ids are silently dropped from the response — no error-path test needed.
    """
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            captured.append(request)
            return [9876]

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-story-rm", RemoveStory(story_id=9876))

    assert result.status == "ok"
    delete_requests = [req for req in captured if isinstance(req, DeleteStoriesRequest)]
    assert len(delete_requests) == 1
    assert delete_requests[0].id == [9876]


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


# --------------------------------------------------------------------------- #
# JoinDiscussionGroup — resolve the linked group from the parent, then join it
# --------------------------------------------------------------------------- #


def _chat_full(linked_chat_id: int | None, *, chat_ids: tuple[int, ...]) -> MagicMock:
    """Build a fake ``messages.ChatFull`` with a ``full_chat`` + ``chats`` list."""
    full = MagicMock()
    full.full_chat = MagicMock(linked_chat_id=linked_chat_id)
    full.chats = [MagicMock(id=cid) for cid in chat_ids]
    return full


@pytest.mark.asyncio
async def test_join_discussion_group_joins_resolved_entity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []
    linked_entity = MagicMock(id=4423)

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            captured.append(request)
            if isinstance(request, GetFullChannelRequest):
                full = MagicMock()
                full.full_chat = MagicMock(linked_chat_id=4423)
                full.chats = [MagicMock(id=999), linked_entity]
                return full
            return None

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-1", JoinDiscussionGroup(channel="@news"))

    assert result.status == "ok"
    assert result.action_type == "join_discussion_group"
    join_reqs = [r for r in captured if isinstance(r, JoinChannelRequest)]
    assert len(join_reqs) == 1
    # joined the resolved linked entity, not the parent channel
    assert join_reqs[0].channel is linked_entity


@pytest.mark.asyncio
async def test_join_discussion_group_already_participant_is_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            if isinstance(request, GetFullChannelRequest):
                return _chat_full(4423, chat_ids=(4423,))
            raise errors.UserAlreadyParticipantError(request=None)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-2", JoinDiscussionGroup(channel="@news"))

    assert result.status == "ok"
    assert result.action_type == "join_discussion_group"


@pytest.mark.asyncio
async def test_join_discussion_group_no_linked_group_classified_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            assert isinstance(request, GetFullChannelRequest)
            return _chat_full(None, chat_ids=())

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-3", JoinDiscussionGroup(channel="@silent"))

    assert result.status == "failed"
    assert result.error_type == "ValueError"
