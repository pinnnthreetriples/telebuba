"""Channel edit, photo, and deletion tests for the Telegram dispatcher."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from telethon import errors
from telethon.tl.functions.channels import (
    DeleteChannelRequest,
    EditPhotoRequest,
    EditTitleRequest,
)
from telethon.tl.functions.messages import EditChatAboutRequest
from telethon.tl.types import InputChatUploadedPhoto

from core.telegram_client import execute
from schemas.telegram_actions import DeleteChannel, EditChannel, SetChannelPhoto
from tests.core.telegram_client.helpers import patch_action_client as _patch_client


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
