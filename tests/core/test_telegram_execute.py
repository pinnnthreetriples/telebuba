"""Tests for ``core.telegram_client.execute`` — the typed-action dispatcher."""

from __future__ import annotations

from contextlib import asynccontextmanager
from io import BytesIO
from types import SimpleNamespace
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
from telethon.tl.functions.photos import (
    DeletePhotosRequest,
    GetUserPhotosRequest,
    UpdateProfilePhotoRequest,
    UploadProfilePhotoRequest,
)
from telethon.tl.functions.stories import (
    CanSendStoryRequest,
    DeleteStoriesRequest,
    SendStoryRequest,
    TogglePinnedRequest,
)
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import InputPhoto

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from core.telegram_client import create_telegram_client, execute
from core.telegram_client._actions import _typing_seconds
from core.telegram_client._pool import TelegramClientPoolError
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
    RemoveProfileMusic,
    RemoveProfilePhoto,
    RemoveStory,
    SendDirectMessage,
    SetMainProfilePhoto,
    SetProfilePhoto,
    ToggleStoryPinned,
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
async def test_execute_update_profile_none_fields_are_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``None`` must reach the TL request as ``None`` (omitted = unchanged).

    Regression guard for the old ``last_name or ""`` coercion, which turned
    "leave my last name alone" into "clear my last name" — and for the
    username: a ``None`` username must not dispatch ``UpdateUsernameRequest``.
    """
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> None:
            captured.append(request)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-4-none", UpdateProfile(first_name="Alice"))

    assert result.status == "ok"
    profile_req = next(req for req in captured if isinstance(req, UpdateProfileRequest))
    assert profile_req.first_name == "Alice"
    assert profile_req.last_name is None
    assert profile_req.about is None
    assert not any(isinstance(req, UpdateUsernameRequest) for req in captured)


@pytest.mark.asyncio
async def test_execute_update_profile_empty_strings_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``""`` must reach the TL requests verbatim — the "clear this field" form.

    ``account.updateProfile`` serializes ``""`` (flag set → server clears) and
    ``UpdateUsernameRequest(username="")`` removes the username.
    """
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> None:
            captured.append(request)

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-4-clear",
        UpdateProfile(first_name="Alice", last_name="", username="", bio=""),
    )

    assert result.status == "ok"
    profile_req = next(req for req in captured if isinstance(req, UpdateProfileRequest))
    assert profile_req.last_name == ""
    assert profile_req.about == ""
    username_req = next(req for req in captured if isinstance(req, UpdateUsernameRequest))
    assert username_req.username == ""


@pytest.mark.asyncio
async def test_execute_update_profile_sends_username_before_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fallible ``UpdateUsernameRequest`` must be dispatched FIRST.

    Sending it after ``UpdateProfileRequest`` half-applied the edit whenever
    the username was occupied/invalid: name/bio had already changed on
    Telegram while the UI reported "nothing saved" and the DB snapshot
    stayed stale.
    """
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> None:
            captured.append(request)

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-4-order",
        UpdateProfile(first_name="Alice", username="alice", bio="Bio"),
    )

    assert result.status == "ok"
    assert isinstance(captured[0], UpdateUsernameRequest)
    assert isinstance(captured[1], UpdateProfileRequest)


@pytest.mark.asyncio
async def test_execute_update_profile_occupied_username_leaves_profile_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused username fails the action BEFORE any profile field changes."""
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> None:
            captured.append(request)
            if isinstance(request, UpdateUsernameRequest):
                raise errors.UsernameOccupiedError(request=None)

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-4-occupied",
        UpdateProfile(first_name="Alice", username="taken", bio="Bio"),
    )

    assert result.status != "ok"
    assert not any(isinstance(req, UpdateProfileRequest) for req in captured)


@pytest.mark.asyncio
async def test_execute_update_profile_username_not_modified_is_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-sending the current username (USERNAME_NOT_MODIFIED) is a no-op, not a failure."""
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> None:
            captured.append(request)
            if isinstance(request, UpdateUsernameRequest):
                raise errors.UsernameNotModifiedError(request=None)

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-4-same-username",
        UpdateProfile(first_name="Alice", username="alice", bio="Bio"),
    )

    assert result.status == "ok"
    assert any(isinstance(req, UpdateProfileRequest) for req in captured)


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
            if isinstance(request, SendStoryRequest):
                # Real shape: ``stories.sendStory`` answers an ``Updates``
                # container (no ``.id`` of its own); the minted story id rides
                # inside as an ``UpdateStory`` carrying ``.story.id``. A filler
                # update first proves the extraction skips non-story updates.
                return SimpleNamespace(
                    updates=[
                        SimpleNamespace(),
                        SimpleNamespace(story=SimpleNamespace(id=777)),
                    ],
                )
            return MagicMock()

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
    from core.telegram_client._story_image import (  # noqa: PLC0415 — internal helper
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


def test_normalize_story_image_rejects_non_image_bytes_with_stable_code() -> None:
    """Undecodable image bytes raise the stable code, never Russian prose.

    Mirrors the video path's ``StoryVideoNormalisationError`` contract
    (non-negotiable #12): ``str(exc)`` must be the locale-neutral code the SPA
    translates, because it travels the ``execute`` → ``error_message`` →
    error-envelope path verbatim.
    """
    from core.telegram_client._story_image import (  # noqa: PLC0415 — internal helper
        StoryImageNormalisationError,
        _normalize_story_image_for_telegram,
    )

    with pytest.raises(StoryImageNormalisationError) as excinfo:
        _normalize_story_image_for_telegram(b"not an image")
    assert str(excinfo.value) == "story_image_invalid"
    assert not any("Ѐ" <= ch <= "ӿ" for ch in str(excinfo.value))
    # The chained cause names the Pillow failure and the real magic bytes, so
    # the telegram_post_story_failed log says what the file actually was.
    assert "magic=" in str(excinfo.value.__cause__)


def test_normalize_story_image_rejects_truncated_file_with_stable_code() -> None:
    """A truncated download maps to the stable code, not raw Pillow prose.

    A cut-off file raises ``OSError`` from ``load()``, not
    ``UnidentifiedImageError`` — both must collapse into the same
    locale-neutral ``story_image_invalid`` code.
    """
    from core.telegram_client._story_image import (  # noqa: PLC0415 — internal helper
        StoryImageNormalisationError,
        _normalize_story_image_for_telegram,
    )

    buffer = BytesIO()
    Image.new("RGB", (200, 200)).save(buffer, format="JPEG")
    truncated = buffer.getvalue()[:400]

    with pytest.raises(StoryImageNormalisationError) as excinfo:
        _normalize_story_image_for_telegram(truncated)
    assert str(excinfo.value) == "story_image_invalid"


def test_normalize_story_image_rejects_decompression_bomb_with_stable_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pillow's decompression-bomb guard maps to the stable code too."""
    from core.telegram_client._story_image import (  # noqa: PLC0415 — internal helper
        StoryImageNormalisationError,
        _normalize_story_image_for_telegram,
    )

    buffer = BytesIO()
    Image.new("RGB", (100, 100)).save(buffer, format="PNG")
    # 100x100 = 10_000 px against a limit of 100 exceeds 2x => hard error.
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)

    with pytest.raises(StoryImageNormalisationError) as excinfo:
        _normalize_story_image_for_telegram(buffer.getvalue())
    assert str(excinfo.value) == "story_image_invalid"


def _jpeg(size: tuple[int, int], colour: tuple[int, int, int]) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("count", "layout"),
    [(2, "v2"), (4, "grid2x2"), (6, "grid2x3")],
)
def test_compose_story_collage_produces_canvas_jpeg(count: int, layout: str) -> None:
    """A collage of representative counts renders one 1080x1920 JPEG."""
    from core.telegram_client._story_image import _compose_story_collage  # noqa: PLC0415

    images = [_jpeg((400, 500), (10 * i, 20, 30)) for i in range(count)]
    out = _compose_story_collage(images, layout)

    with Image.open(BytesIO(out)) as result:
        assert result.size == (1080, 1920)
        assert result.format == "JPEG"


def test_compose_story_collage_default_layout_is_first_for_count() -> None:
    from core.telegram_client._story_image import _default_collage_layout  # noqa: PLC0415

    assert _default_collage_layout(2) == "v2"
    assert _default_collage_layout(3) == "v3"


def test_compose_story_collage_unknown_layout_raises() -> None:
    from core.telegram_client._story_image import (  # noqa: PLC0415
        StoryCollageLayoutError,
        _compose_story_collage,
    )

    images = [_jpeg((100, 100), (0, 0, 0)), _jpeg((100, 100), (255, 255, 255))]
    with pytest.raises(StoryCollageLayoutError) as excinfo:
        _compose_story_collage(images, "grid2x2")  # grid2x2 is a count-4 layout
    assert str(excinfo.value) == "story_collage_unknown_layout"
    assert "unknown collage layout" in str(excinfo.value.__cause__)


def test_compose_story_collage_unsupported_count_raises() -> None:
    from core.telegram_client._story_image import (  # noqa: PLC0415
        StoryCollageLayoutError,
        _compose_story_collage,
    )

    images = [_jpeg((100, 100), (0, 0, 0))] * 7
    with pytest.raises(StoryCollageLayoutError) as excinfo:
        _compose_story_collage(images, "v2")
    assert "unsupported collage image count" in str(excinfo.value.__cause__)


def test_compose_story_collage_rejects_undecodable_image() -> None:
    from core.telegram_client._story_image import (  # noqa: PLC0415
        StoryImageNormalisationError,
        _compose_story_collage,
    )

    with pytest.raises(StoryImageNormalisationError):
        _compose_story_collage([_jpeg((100, 100), (0, 0, 0)), b"not an image"], "v2")


@pytest.mark.asyncio
async def test_execute_post_story_collage_uploads_single_composite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A collage PostStory stitches its images into ONE uploaded 1080x1920 photo."""
    captured: list[object] = []
    uploaded_bytes: list[bytes] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, entity: str) -> object:
            assert entity == "me"
            return MagicMock()

        async def upload_file(self, file: BytesIO, *, file_name: str) -> object:  # noqa: ARG002
            uploaded_bytes.append(file.read())
            return MagicMock()

        async def __call__(self, request: object) -> object:
            captured.append(request)
            if isinstance(request, SendStoryRequest):
                # Real Updates shape — see the single-photo story test above.
                return SimpleNamespace(
                    updates=[SimpleNamespace(story=SimpleNamespace(id=555))],
                )
            return MagicMock()

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-collage",
        PostStory(
            filename="story.jpg",
            content=_jpeg((400, 500), (200, 0, 0)),
            media_kind="image",
            extra_images=[_jpeg((400, 500), (0, 200, 0))],
            collage_layout="h2",
        ),
    )

    assert result.status == "ok"
    assert result.message_id == 555
    assert any(isinstance(req, SendStoryRequest) for req in captured)
    # Exactly one photo hit upload_file, already normalised to the story canvas.
    assert len(uploaded_bytes) == 1
    with Image.open(BytesIO(uploaded_bytes[0])) as sent:
        assert sent.size == (1080, 1920)


@pytest.mark.asyncio
async def test_execute_post_story_collage_unknown_layout_surfaces_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bad collage layout id fails with the stable locale-neutral code (#12)."""

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, entity: str) -> object:
            assert entity == "me"
            return MagicMock()

        async def upload_file(self, _file: BytesIO, *, file_name: str) -> object:  # noqa: ARG002
            msg = "upload must not run for an unresolved layout"
            raise AssertionError(msg)

        async def __call__(self, _request: object) -> object:
            return MagicMock(id=1)

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-collage-bad",
        PostStory(
            filename="story.jpg",
            content=_jpeg((400, 500), (200, 0, 0)),
            media_kind="image",
            extra_images=[_jpeg((400, 500), (0, 200, 0))],
            collage_layout="grid2x2",  # a count-4 layout requested for 2 images
        ),
    )

    assert result.status == "failed"
    assert result.error_message == "story_collage_unknown_layout"


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
async def test_execute_remove_profile_music_ok_when_server_confirms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            assert isinstance(request, SaveMusicRequest)
            assert request.unsave is True
            return True

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-music-remove",
        RemoveProfileMusic(file_id=5, access_hash=6, file_reference=b"\x01"),
    )

    assert result.status == "ok"


@pytest.mark.asyncio
async def test_execute_remove_profile_music_errors_when_server_says_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``account.saveMusic(unsave=True)`` answering ``False`` must surface an error.

    Telegram answers ``False`` for a stale/unknown ``InputDocument`` — a silent
    no-op that used to be logged as a successful removal while the track stayed
    on the profile (mirrors the photo-remove empty-vector guard).
    """

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            assert isinstance(request, SaveMusicRequest)
            return False

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-music-remove-noop",
        RemoveProfileMusic(file_id=5, access_hash=6, file_reference=b"\x01"),
    )

    assert result.status != "ok"
    assert result.error_message


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

        async def __call__(self, request: object) -> object:
            captured.append(request)
            # ``DeletePhotosRequest`` returns the vector of ids it deleted.
            return [4242]

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
async def test_execute_remove_profile_photo_errors_when_telegram_deletes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty delete vector means the photo stayed — surface an error, not success.

    Telegram silently no-ops a stale or unrecognised ``InputPhoto`` (empty
    vector). That must NOT be reported as a removal — the false success is what
    let JS-rounded int64 ids "delete" the same photo over and over.
    """

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:
            # Telegram recognised no photo to delete.
            return []

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-photo-remove-noop",
        RemoveProfilePhoto(photo_id=4242, access_hash=7, file_reference=b"\x01\x02"),
    )

    assert result.status != "ok"
    assert result.error_message


# Fresh id triple the fake GetUserPhotos re-resolves — deliberately different
# from the stale snapshot ref the action carries, to prove re-resolution.
_FRESH_ACCESS_HASH = 99
_FRESH_REFERENCE = b"\xaa\xbb"


def _set_main_client(  # noqa: PLR0913 - keyword-only knobs for the make-main fake
    captured: list[object],
    *,
    target_id: int,
    updated_id: int | None,
    history_ids: list[int] | None = None,
    history_ids_after: list[int] | None = None,
    avatar_ids: tuple[int | None, int | None] = (None, None),
) -> object:
    """Fake client for the make-main flow: GetUserPhotos → getFullUser → promote.

    ``history_ids`` seeds the FIRST ``GetUserPhotos`` (default: just ``target_id``);
    ``history_ids_after`` seeds later ones — a fresh read models REPLACE (the
    promote consumed the original id), while one still listing the original id
    models replication lag of the read. ``avatar_ids`` is what
    ``users.getFullUser`` reports before/after the promote. ``updated_id`` is
    the id ``updateProfilePhoto`` returns (``None`` → a bare result without
    ``photo``). Any ``DeletePhotosRequest`` would "succeed" (returns the asked
    ids) — on live Telegram exactly such a delete, issued against a lagged read,
    destroyed the previous main avatar, so the tests must prove it is never
    even sent.
    """
    ids = history_ids if history_ids is not None else [target_id]
    ids_after = history_ids_after if history_ids_after is not None else ids
    calls = {"get_user_photos": 0, "get_full_user": 0}

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            captured.append(request)
            if isinstance(request, GetUserPhotosRequest):
                calls["get_user_photos"] += 1
                seed = ids if calls["get_user_photos"] == 1 else ids_after
                photos = [
                    SimpleNamespace(
                        id=pid,
                        access_hash=_FRESH_ACCESS_HASH,
                        file_reference=_FRESH_REFERENCE,
                    )
                    for pid in seed
                ]
                return SimpleNamespace(photos=photos)
            if isinstance(request, GetFullUserRequest):
                calls["get_full_user"] += 1
                avatar_id = avatar_ids[0] if calls["get_full_user"] == 1 else avatar_ids[1]
                profile_photo = SimpleNamespace(id=avatar_id) if avatar_id is not None else None
                return SimpleNamespace(full_user=SimpleNamespace(profile_photo=profile_photo))
            if isinstance(request, DeletePhotosRequest):
                # A live server would report success here — and the data is gone.
                return [photo.id for photo in request.id if isinstance(photo, InputPhoto)]
            # updateProfilePhoto returns photos.Photo with (maybe fresh) id.
            photo = SimpleNamespace(id=updated_id) if updated_id is not None else None
            return SimpleNamespace(photo=photo)

    return FakeClient()


def _patch_id_flow_log(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, str, dict[str, object]]]:
    """Record the gateway's ``log_event`` calls in ``_media`` (owning submodule)."""
    events: list[tuple[str, str, dict[str, object]]] = []

    async def _fake_log(
        level: str,
        event: str,
        _account_id: str | None = None,
        extra: dict[str, object] | None = None,
    ) -> None:
        events.append((level, event, extra or {}))

    monkeypatch.setattr("core.telegram_client._media.log_event", _fake_log)
    return events


@pytest.mark.asyncio
async def test_execute_set_main_photo_replaces_photo_and_logs_id_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """«Сделать основным» = REPLACE: promote consumes the original id, deletes nothing.

    True server semantics (official-client parity): ``updateProfilePhoto`` on a
    history photo consumes the original id (``big_id``) and mints a NEW id (555)
    at the front — count unchanged, previous main keeps its id one slot down.
    The gateway promotes from a fresh re-resolved ``InputPhoto`` (int64 id
    intact past 2^53), sends no delete, and logs the id flow before/after so a
    live run is verifiable from debug.log (new avatar id == promoted id).
    """
    captured: list[object] = []
    big_id = 9_007_199_254_740_993  # 2^53 + 1
    old_main = 111
    filler = 222
    events = _patch_id_flow_log(monkeypatch)
    _patch_client(
        monkeypatch,
        _set_main_client(
            captured,
            target_id=big_id,
            updated_id=555,
            history_ids=[old_main, filler, big_id],
            # Fresh post-promote read: original id CONSUMED, new id at front.
            history_ids_after=[555, old_main, filler],
            avatar_ids=(old_main, 555),
        ),
    )

    result = await execute(
        "acc-photo-main",
        # Snapshot ref is intentionally STALE — the gateway must ignore it.
        SetMainProfilePhoto(photo_id=big_id, access_hash=7, file_reference=b"\x01\x02"),
    )

    assert result.status == "ok"
    assert [req for req in captured if isinstance(req, DeletePhotosRequest)] == []
    updates = [req for req in captured if isinstance(req, UpdateProfilePhotoRequest)]
    assert len(updates) == 1
    sent = updates[0].id
    # ``UpdateProfilePhotoRequest.id`` is typed as ``InputPhoto | InputPhotoEmpty``;
    # narrow with an isinstance so ty knows the access_hash / file_reference
    # attributes are present.
    assert isinstance(sent, InputPhoto)
    assert sent.id == big_id
    # The FRESH access_hash / file_reference are used, not the stale snapshot ones.
    assert sent.access_hash == _FRESH_ACCESS_HASH
    assert sent.file_reference == _FRESH_REFERENCE
    # Two GetUserPhotos (before + after) — the "after" one only feeds the log.
    assert len([req for req in captured if isinstance(req, GetUserPhotosRequest)]) == 2
    assert len([req for req in captured if isinstance(req, GetFullUserRequest)]) == 2
    # Both id-flow events fired with the full picture for live verification.
    flow = [(event, extra) for _level, event, extra in events]
    assert [event for event, _extra in flow] == [
        "telegram_set_main_id_flow",
        "telegram_set_main_id_flow",
    ]
    before, after = flow[0][1], flow[1][1]
    assert before["phase"] == "before"
    assert before["target_photo_id"] == big_id
    assert before["history_ids"] == [old_main, filler, big_id]
    assert before["current_avatar_id"] == old_main
    assert after["phase"] == "after"
    assert after["target_photo_id"] == big_id
    assert after["history_ids"] == [555, old_main, filler]
    # The promoted photo's NEW identity — must match the new current avatar.
    assert after["promoted_photo_id"] == 555
    assert after["current_avatar_id"] == after["promoted_photo_id"]


@pytest.mark.asyncio
async def test_set_main_profile_photo_never_deletes_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Data-loss regression: even on a LAGGED post-promote read, never delete.

    Live evidence (debug.log 2026-07-13): a post-promote ``GetUserPhotos`` can
    still list the consumed original id — replication lag of the read, not a
    real leftover. The old "dedup" step deleted against exactly this view and,
    because own-profile deletes resolve by id alone, destroyed the UNRELATED
    previous main avatar (18:37:17 run). Whatever the post-promote read says,
    ``DeletePhotosRequest`` must never be issued from this action.
    """
    captured: list[object] = []
    big_id = 9_007_199_254_740_993  # 2^53 + 1
    old_main = 111
    _patch_client(
        monkeypatch,
        _set_main_client(
            captured,
            target_id=big_id,
            updated_id=555,
            history_ids=[old_main, big_id],
            # LAGGED post-promote read: the consumed original id still listed.
            history_ids_after=[555, old_main, big_id],
            avatar_ids=(old_main, 555),
        ),
    )

    result = await execute(
        "acc-photo-main",
        SetMainProfilePhoto(photo_id=big_id, access_hash=7, file_reference=b"\x01\x02"),
    )

    assert result.status == "ok"
    # THE regression assertion: no delete request of any kind, ever.
    assert [req for req in captured if isinstance(req, DeletePhotosRequest)] == []
    assert len([req for req in captured if isinstance(req, UpdateProfilePhotoRequest)]) == 1


@pytest.mark.asyncio
async def test_execute_set_main_photo_raises_when_target_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A target id no longer in the history is a surfaced error, not a silent no-op."""
    captured: list[object] = []
    _patch_client(
        monkeypatch,
        _set_main_client(
            captured,
            target_id=4242,
            updated_id=4242,
            history_ids=[9999],  # some OTHER photo, not the target
        ),
    )

    result = await execute(
        "acc-photo-main",
        SetMainProfilePhoto(photo_id=4242, access_hash=7, file_reference=b"\x01\x02"),
    )

    assert result.status != "ok"
    assert result.error_message
    # Never promote or delete when the target can't be re-resolved.
    assert [req for req in captured if isinstance(req, UpdateProfilePhotoRequest)] == []
    assert [req for req in captured if isinstance(req, DeletePhotosRequest)] == []


@pytest.mark.asyncio
async def test_execute_set_main_photo_tolerates_bare_server_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No avatar and a bare promote result still succeed — ids logged as None.

    ``getFullUser`` without a ``profile_photo`` and an ``updateProfilePhoto``
    result without ``photo.id`` must not break the action; the id-flow log
    records ``None`` and, as always, nothing is deleted.
    """
    captured: list[object] = []
    events = _patch_id_flow_log(monkeypatch)
    _patch_client(
        monkeypatch,
        _set_main_client(captured, target_id=4242, updated_id=None),
    )

    result = await execute(
        "acc-photo-main",
        SetMainProfilePhoto(photo_id=4242, access_hash=7, file_reference=b"\x01\x02"),
    )

    assert result.status == "ok"
    assert [req for req in captured if isinstance(req, DeletePhotosRequest)] == []
    assert len([req for req in captured if isinstance(req, UpdateProfilePhotoRequest)]) == 1
    before, after = (extra for _level, _event, extra in events)
    assert before["current_avatar_id"] is None
    assert after["current_avatar_id"] is None
    assert after["promoted_photo_id"] is None


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
@pytest.mark.parametrize("pinned", [True, False])
async def test_execute_toggle_story_pinned_sends_toggle_request(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pinned: bool,
) -> None:
    """``stories.togglePinned`` carries the target ``pinned`` state + single id.

    Pinning keeps the story on the profile forever; unpinning drops it back to
    the 24 h active window. Both directions hit the same request, differing only
    in the ``pinned`` flag.
    """
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            captured.append(request)
            return [3210]

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-story-pin", ToggleStoryPinned(story_id=3210, pinned=pinned))

    assert result.status == "ok"
    toggles = [req for req in captured if isinstance(req, TogglePinnedRequest)]
    assert len(toggles) == 1
    assert toggles[0].id == [3210]
    assert toggles[0].pinned is pinned


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [
        TelegramClientPoolError("acc-7", RuntimeError("connect failed")),
        ConnectionError("socket closed"),
        TimeoutError("handshake timed out"),
    ],
)
async def test_execute_classifies_infrastructure_failures_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
) -> None:
    """Pool/socket/timeout failures are ``unavailable``, not a client-fault ``failed``.

    ``failed`` reaches the API as 400 bad_request; an internal outage must map
    to 503 instead (see ``api/errors.py``), so the executor classifies the
    infrastructure family separately from generic action failures.
    """

    async def failing_get_client(_account_id: str) -> object:
        raise exc

    async def fake_fetch(account_id: str) -> object:
        return MagicMock(session_name=account_id)

    monkeypatch.setattr("core.telegram_client._actions.get_client", failing_get_client)
    monkeypatch.setattr("core.telegram_client._actions.fetch_account", fake_fetch)

    result = await execute("acc-7", JoinChannel(channel="@hot"))

    assert result.status == "unavailable"
    assert result.error_type == type(exc).__name__


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
