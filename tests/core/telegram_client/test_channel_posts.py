"""Channel-post publication and mutation tests for the Telegram dispatcher."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from telethon import errors
from telethon.tl.types import DocumentAttributeVideo

from core.telegram_client import execute
from schemas.telegram_actions import (
    DeleteChannelPost,
    EditChannelPost,
    PublishChannelPost,
)
from tests.core.telegram_client.helpers import patch_action_client as _patch_client


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
