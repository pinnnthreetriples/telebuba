"""Tests for the channel-management write dispatcher (``_channels.py``)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from telethon import errors
from telethon.tl.functions.channels import (
    CheckUsernameRequest,
    CreateChannelRequest,
    DeleteChannelRequest,
    EditPhotoRequest,
    EditTitleRequest,
    UpdateUsernameRequest,
)
from telethon.tl.functions.messages import EditChatAboutRequest
from telethon.tl.types import DocumentAttributeVideo, InputChatUploadedPhoto

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from core.telegram_client import execute
from schemas.telegram_actions import (
    CreateChannel,
    DeleteChannel,
    DeleteChannelPost,
    EditChannel,
    EditChannelPost,
    PublishChannelPost,
    SetChannelPhoto,
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
    """Replace ``get_client`` with a coroutine that returns ``client``."""

    async def fake_get_client(_account_id: str) -> object:
        return client

    async def fake_fetch(account_id: str):
        return MagicMock(session_name=account_id)

    monkeypatch.setattr("core.telegram_client._actions.get_client", fake_get_client)
    monkeypatch.setattr("core.telegram_client._actions.fetch_account", fake_fetch)


class _ChannelClient:
    """Fake client resolving any channel id and capturing every request."""

    def __init__(self) -> None:
        self.captured: list[object] = []
        self.entity = MagicMock(name="input-channel")

    async def connect(self) -> None:
        return None

    async def get_input_entity(self, _peer: object) -> object:
        return self.entity

    async def __call__(self, request: object) -> object:
        self.captured.append(request)
        return MagicMock()


# --------------------------------------------------------------------------- #
# CreateChannel
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_create_channel_sends_broadcast_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No username → straight to CreateChannelRequest with broadcast=True."""
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            captured.append(request)
            if isinstance(request, CreateChannelRequest):
                return SimpleNamespace(chats=[SimpleNamespace(id=4242)])
            return MagicMock()

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-ch", CreateChannel(title="My channel", about="Desc"))

    assert result.status == "ok"
    assert result.action_type == "channel_create"
    creates = [r for r in captured if isinstance(r, CreateChannelRequest)]
    assert len(creates) == 1
    assert creates[0].title == "My channel"
    assert creates[0].about == "Desc"
    assert creates[0].broadcast is True
    assert creates[0].megagroup is False
    # No username → no availability probe, no username assignment.
    assert not any(isinstance(r, CheckUsernameRequest) for r in captured)
    assert not any(isinstance(r, UpdateUsernameRequest) for r in captured)
    # The new channel id rides back as an int64 STRING.
    assert result.channel_id == "4242"


@pytest.mark.asyncio
async def test_create_channel_with_username_assigns_after_precheck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            captured.append(request)
            if isinstance(request, CheckUsernameRequest):
                return True
            if isinstance(request, CreateChannelRequest):
                return SimpleNamespace(chats=[SimpleNamespace(id=987)])
            return MagicMock()

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-ch-user",
        CreateChannel(title="Public", username="my_channel"),
    )

    assert result.status == "ok"
    assert result.channel_id == "987"
    # Probe BEFORE create, username assignment AFTER create — in that order.
    kinds = [type(r).__name__ for r in captured]
    assert kinds.index("CheckUsernameRequest") < kinds.index("CreateChannelRequest")
    assert kinds.index("CreateChannelRequest") < kinds.index("UpdateUsernameRequest")
    update = next(r for r in captured if isinstance(r, UpdateUsernameRequest))
    assert update.username == "my_channel"


@pytest.mark.asyncio
async def test_create_channel_occupied_username_creates_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused handle must fail BEFORE anything is created (no orphan)."""
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            captured.append(request)
            if isinstance(request, CheckUsernameRequest):
                return False
            return MagicMock()

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-ch-occupied",
        CreateChannel(title="Public", username="taken_name"),
    )

    assert result.status == "failed"
    assert result.error_message == "channel_username_occupied"
    assert not any(isinstance(r, CreateChannelRequest) for r in captured)
    assert result.channel_id is None


@pytest.mark.asyncio
async def test_create_channel_username_failure_after_create_carries_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CHANNELS_ADMIN_PUBLIC_TOO_MUCH fires on the ASSIGNMENT, not the pre-check.

    The channel then already exists (private): the failed result must carry
    its id so the caller can adopt it instead of re-creating a duplicate, and
    NOTHING may be deleted (never auto-delete - repo data-safety rule).
    """
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            captured.append(request)
            if isinstance(request, CheckUsernameRequest):
                return True
            if isinstance(request, CreateChannelRequest):
                return SimpleNamespace(chats=[SimpleNamespace(id=987)])
            if isinstance(request, UpdateUsernameRequest):
                raise errors.ChannelsAdminPublicTooMuchError(request=None)
            return MagicMock()

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-ch-postfail",
        CreateChannel(title="Public", username="my_channel"),
    )

    assert result.status == "failed"
    assert result.error_message == "channels_admin_public_too_much"
    # The created channel id rides the FAILED result (int64 as string).
    assert result.channel_id == "987"
    assert not any(isinstance(r, DeleteChannelRequest) for r in captured)


@pytest.mark.asyncio
async def test_create_channel_without_returned_entity_surfaces_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            if isinstance(request, CreateChannelRequest):
                return SimpleNamespace(chats=[])
            return MagicMock()

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-ch-empty", CreateChannel(title="Ghost"))

    assert result.status == "failed"
    assert result.error_message == "channel_create_failed"


@pytest.mark.asyncio
async def test_create_channel_maps_channels_too_much_to_stable_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            if isinstance(request, CreateChannelRequest):
                raise errors.ChannelsTooMuchError(request=None)
            return MagicMock()

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-ch-toomuch", CreateChannel(title="One more"))

    assert result.status == "failed"
    assert result.error_message == "channels_too_much"


@pytest.mark.asyncio
async def test_create_channel_flood_wait_reaches_flood_ladder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flood errors must NOT be swallowed by the channel RPC mapping."""

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:
            raise errors.FloodWaitError(request=None, capture=33)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-ch-flood", CreateChannel(title="Flooded"))

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 33


@pytest.mark.asyncio
async def test_channel_unmapped_rpc_error_passes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:
            raise errors.ChatWriteForbiddenError(request=None)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-ch-other", CreateChannel(title="Nope"))

    assert result.status == "failed"
    assert result.error_type == "ChatWriteForbiddenError"


# --------------------------------------------------------------------------- #
# EditChannel / SetChannelPhoto / DeleteChannel
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_edit_channel_sends_title_and_about(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _ChannelClient()
    _patch_client(monkeypatch, client)

    result = await execute(
        "acc-edit",
        EditChannel(channel_id=42, title="New title", about="New about"),
    )

    assert result.status == "ok"
    titles = [r for r in client.captured if isinstance(r, EditTitleRequest)]
    abouts = [r for r in client.captured if isinstance(r, EditChatAboutRequest)]
    assert len(titles) == 1
    assert titles[0].title == "New title"
    assert len(abouts) == 1
    assert abouts[0].about == "New about"


@pytest.mark.asyncio
async def test_edit_channel_title_only_skips_about(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _ChannelClient()
    _patch_client(monkeypatch, client)

    result = await execute("acc-edit-title", EditChannel(channel_id=42, title="Only title"))

    assert result.status == "ok"
    assert any(isinstance(r, EditTitleRequest) for r in client.captured)
    assert not any(isinstance(r, EditChatAboutRequest) for r in client.captured)


@pytest.mark.asyncio
async def test_edit_channel_not_modified_is_idempotent_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-saving unchanged values must stay ok (NotModified suppressed)."""
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, _peer: object) -> object:
            return MagicMock()

        async def __call__(self, request: object) -> object:
            captured.append(request)
            if isinstance(request, EditTitleRequest):
                raise errors.ChatNotModifiedError(request=None)
            if isinstance(request, EditChatAboutRequest):
                raise errors.ChatAboutNotModifiedError(request=None)
            return MagicMock()

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-edit-same",
        EditChannel(channel_id=42, title="Same", about="Same"),
    )

    assert result.status == "ok"
    # Both requests were still attempted (suppression, not skipping).
    assert any(isinstance(r, EditTitleRequest) for r in captured)
    assert any(isinstance(r, EditChatAboutRequest) for r in captured)


@pytest.mark.asyncio
async def test_set_channel_photo_uploads_then_edits_photo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []
    uploaded = MagicMock(name="uploaded-file")

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, _peer: object) -> object:
            return MagicMock()

        async def upload_file(self, _file: object, *, file_name: str) -> object:
            assert file_name == "logo.png"
            return uploaded

        async def __call__(self, request: object) -> object:
            captured.append(request)
            return MagicMock()

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-photo",
        SetChannelPhoto(channel_id=42, filename="logo.png", content=b"png"),
    )

    assert result.status == "ok"
    edits = [r for r in captured if isinstance(r, EditPhotoRequest)]
    assert len(edits) == 1
    assert isinstance(edits[0].photo, InputChatUploadedPhoto)
    assert edits[0].photo.file is uploaded


@pytest.mark.asyncio
async def test_delete_channel_sends_delete_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _ChannelClient()
    _patch_client(monkeypatch, client)

    result = await execute("acc-del", DeleteChannel(channel_id=42))

    assert result.status == "ok"
    deletes = [r for r in client.captured if isinstance(r, DeleteChannelRequest)]
    assert len(deletes) == 1
    assert deletes[0].channel is client.entity


@pytest.mark.asyncio
async def test_unresolvable_channel_maps_to_channel_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, peer: object) -> object:
            msg = f"Could not find the input entity for {peer!r}"
            raise ValueError(msg)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-missing", DeleteChannel(channel_id=999))

    assert result.status == "failed"
    assert result.error_message == "channel_not_found"


# --------------------------------------------------------------------------- #
# Channel posts
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_publish_text_post_returns_message_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entity = MagicMock(name="input-channel")

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, _peer: object) -> object:
            return entity

        async def send_message(self, target: object, text: str) -> object:
            assert target is entity
            assert text == "hello subscribers"
            return MagicMock(id=555)

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-post-text",
        PublishChannelPost(channel_id=42, text="hello subscribers"),
    )

    assert result.status == "ok"
    assert result.action_type == "channel_post_publish"
    assert result.message_id == 555


@pytest.mark.asyncio
async def test_publish_photo_post_sends_file_with_caption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: dict[str, object] = {}

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, _peer: object) -> object:
            return MagicMock()

        async def send_file(self, _entity: object, file: object, **kwargs: object) -> object:
            sent["file"] = file
            sent.update(kwargs)
            return MagicMock(id=777)

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-post-photo",
        PublishChannelPost(
            channel_id=42,
            text="nice pic",
            filename="pic.jpg",
            content=b"jpeg-bytes",
            media_kind="photo",
        ),
    )

    assert result.status == "ok"
    assert result.message_id == 777
    assert sent["caption"] == "nice pic"
    assert sent["file_name"] == "pic.jpg"


@pytest.mark.asyncio
async def test_publish_video_post_normalises_and_sets_video_attributes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Video posts re-encode through the channel normaliser (source resolution)."""
    sent: dict[str, object] = {}
    normalised: list[bytes] = []

    async def fake_normalize(content: bytes) -> tuple[bytes, bytes, int, int, int]:
        normalised.append(content)
        return (b"encoded-mp4", b"thumb-jpg", 7, 640, 360)

    monkeypatch.setattr(
        "core.telegram_client._channels.normalize_channel_video_for_telegram",
        fake_normalize,
    )

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, _peer: object) -> object:
            return MagicMock()

        async def send_file(self, _entity: object, file: object, **kwargs: object) -> object:
            sent["file"] = file
            sent.update(kwargs)
            return MagicMock(id=888)

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-post-video",
        PublishChannelPost(
            channel_id=42,
            text="watch this",
            filename="clip.mp4",
            content=b"raw-video",
            media_kind="video",
        ),
    )

    assert result.status == "ok"
    assert result.message_id == 888
    assert normalised == [b"raw-video"]
    assert sent["caption"] == "watch this"
    assert sent["mime_type"] == "video/mp4"
    assert sent["thumb"] == b"thumb-jpg"
    attributes = sent["attributes"]
    assert isinstance(attributes, list)
    video_attr = attributes[0]
    assert isinstance(video_attr, DocumentAttributeVideo)
    assert video_attr.duration == 7
    assert video_attr.w == 640
    assert video_attr.h == 360
    assert video_attr.supports_streaming is True


@pytest.mark.asyncio
async def test_edit_post_updates_text(monkeypatch: pytest.MonkeyPatch) -> None:
    edited: list[tuple[int, str]] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, _peer: object) -> object:
            return MagicMock()

        async def edit_message(self, _entity: object, post_id: int, text: str) -> object:
            edited.append((post_id, text))
            return MagicMock()

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-edit-post",
        EditChannelPost(channel_id=42, post_id=10, text="fixed typo"),
    )

    assert result.status == "ok"
    assert edited == [(10, "fixed typo")]


@pytest.mark.asyncio
async def test_edit_post_not_modified_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, _peer: object) -> object:
            return MagicMock()

        async def edit_message(self, _entity: object, _post_id: int, _text: str) -> object:
            raise errors.MessageNotModifiedError(request=None)

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-edit-same-post",
        EditChannelPost(channel_id=42, post_id=10, text="same text"),
    )

    assert result.status == "ok"


@pytest.mark.asyncio
async def test_edit_post_time_expired_maps_to_stable_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, _peer: object) -> object:
            return MagicMock()

        async def edit_message(self, _entity: object, _post_id: int, _text: str) -> object:
            raise errors.MessageEditTimeExpiredError(request=None)

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-edit-old-post",
        EditChannelPost(channel_id=42, post_id=10, text="too late"),
    )

    assert result.status == "failed"
    assert result.error_message == "message_edit_time_expired"


@pytest.mark.asyncio
async def test_edit_post_unknown_id_maps_to_post_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, _peer: object) -> object:
            return MagicMock()

        async def edit_message(self, _entity: object, _post_id: int, _text: str) -> object:
            raise errors.MessageIdInvalidError(request=None)

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-edit-gone-post",
        EditChannelPost(channel_id=42, post_id=10, text="gone"),
    )

    assert result.status == "failed"
    assert result.error_message == "channel_post_not_found"


@pytest.mark.asyncio
async def test_delete_post_deletes_by_id(monkeypatch: pytest.MonkeyPatch) -> None:
    deleted: list[list[int]] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, _peer: object) -> object:
            return MagicMock()

        async def delete_messages(self, _entity: object, message_ids: list[int]) -> object:
            deleted.append(message_ids)
            return MagicMock()

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-del-post",
        DeleteChannelPost(channel_id=42, post_id=15),
    )

    assert result.status == "ok"
    assert deleted == [[15]]
