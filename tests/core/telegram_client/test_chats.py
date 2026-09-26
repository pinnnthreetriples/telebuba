from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from telethon import hints, utils
from telethon.tl.types import (
    Channel,
    ChatPhotoEmpty,
    DocumentAttributeFilename,
    DocumentAttributeVideo,
    InputFile,
    PeerChannel,
    User,
)

from core.telegram_client import _chat_writes, _chats
from schemas.chats import ChatUpload

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Any


def _cursor(data: dict[str, object]) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")


class _DialogClient:
    def __init__(self, pages: Sequence[Sequence[object]]) -> None:
        self.pages = list(pages)
        self.calls: list[dict[str, object]] = []

    async def get_input_entity(self, peer: object) -> object:
        return peer

    def iter_dialogs(self, **kwargs: object):
        self.calls.append(kwargs)
        page = self.pages.pop(0)

        async def _items():
            for item in page:
                yield item

        return _items()


@pytest.mark.asyncio
async def test_dialog_pages_include_archived_empty_dialog_and_use_last_known_message_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    last_message = SimpleNamespace(
        id=41,
        date=datetime(2026, 1, 1, tzinfo=UTC),
        message="last visible",
        out=False,
        sender_id=7,
        media=None,
    )
    dialogs = [
        SimpleNamespace(
            entity=User(id=7, first_name="Person"),
            title="Person",
            message=last_message,
            folder_id=0,
            unread_count=2,
        ),
        SimpleNamespace(
            entity=Channel(id=8, title="Archived", photo=ChatPhotoEmpty(), date=None),
            title="Archived",
            message=None,
            folder_id=1,
            unread_count=0,
        ),
        SimpleNamespace(
            entity=User(id=9, first_name="Next"),
            title="Next",
            message=None,
            folder_id=0,
            unread_count=0,
        ),
    ]
    client = _DialogClient([dialogs, []])
    monkeypatch.setattr(_chats, "get_client", lambda _account_id: _async_result(client))

    items, cursor = await _chats.list_dialogs("acc-1", limit=2, cursor=None)
    assert len(items) == 2
    assert items[1].is_archived is True
    assert items[1].last_message is None
    assert cursor is not None
    payload = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    assert payload == {
        "peer_type": "channel",
        "peer_id": "8",
        "message_id": 41,
        "date": last_message.date.isoformat(),
    }

    next_items, next_cursor = await _chats.list_dialogs("acc-1", limit=2, cursor=cursor)
    assert next_items == []
    assert next_cursor is None
    assert client.calls[1]["ignore_pinned"] is True
    assert client.calls[1]["offset_peer"] == PeerChannel(channel_id=8)
    assert client.calls[1]["offset_id"] == 41


async def _async_result(value: object) -> object:
    return value


def test_malformed_dialog_cursor_is_a_stable_gateway_error() -> None:
    with pytest.raises(_chats.ChatGatewayError, match="chat_cursor_invalid"):
        _chats._cursor_decode("not-a-valid-cursor")


class _ClosableStream:
    def __init__(self) -> None:
        self.closed = False

    def __aiter__(self):
        async def _chunks():
            yield b"a"
            yield b"b"

        return _chunks()

    async def close(self) -> None:
        self.closed = True


class _MediaClient:
    def __init__(self) -> None:
        self.stream = _ClosableStream()

    async def get_input_entity(self, _peer: object) -> object:
        return object()

    async def get_entity(self, _peer: object) -> object:
        return object()

    async def get_messages(self, _peer: object, *, ids: int) -> object:
        return SimpleNamespace(
            id=ids,
            media=SimpleNamespace(photo=object(), document=None),
            file=SimpleNamespace(name="../photo.jpg", mime_type="image/jpeg", size=2),
        )

    def iter_download(self, _media: object) -> _ClosableStream:
        return self.stream


@pytest.mark.asyncio
async def test_media_download_stream_closes_iterator_and_sanitizes_filename(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _MediaClient()
    monkeypatch.setattr(_chat_writes, "get_client", lambda _account_id: _async_result(client))
    monkeypatch.setattr(_chat_writes, "_media_kind", lambda _message: "image")
    filename, mime, size, chunks = await _chat_writes.media_download("acc-1", "user", 7, 12, 0)

    assert filename == "photo.jpg"
    assert mime == "image/jpeg"
    assert size == 2
    assert [chunk async for chunk in chunks] == [b"a", b"b"]
    assert client.stream.closed is True


@pytest.mark.asyncio
async def test_broadcast_admin_write_is_not_rejected_by_chat_permission_probe() -> None:
    class Client:
        async def get_permissions(self, entity: hints.EntityLike, user: str) -> Any:
            del entity, user
            pytest.fail("broadcast post permission is not the group send_messages bit")

    await _chat_writes._ensure_writable(
        Client(),
        "channel",
        cast("hints.EntityLike", SimpleNamespace(broadcast=True)),
    )


@pytest.mark.asyncio
async def test_confirmed_group_mute_is_rejected() -> None:
    class Client:
        async def get_permissions(self, entity: hints.EntityLike, user: str) -> object:
            del entity, user
            return SimpleNamespace(banned_rights=SimpleNamespace(send_messages=True))

    with pytest.raises(_chats.ChatGatewayError, match="chat_write_forbidden"):
        await _chat_writes._ensure_writable(
            Client(), "chat", cast("hints.EntityLike", SimpleNamespace())
        )


class _SendClient:
    def __init__(self) -> None:
        self.file_args: dict[str, object] | None = None
        self.text_args: dict[str, object] | None = None
        self.upload_names: list[str] = []

    async def upload_file(self, _file: object, *, file_name: str) -> object:
        self.upload_names.append(file_name)
        return InputFile(id=1, parts=1, name=file_name, md5_checksum="")

    async def get_input_entity(self, _peer: object) -> object:
        return object()

    async def get_entity(self, _peer: object) -> object:
        return User(id=3, first_name="A")

    async def send_file(
        self, _peer: object, files: list[InputFile], **kwargs: object
    ) -> list[object]:
        self.file_args = {
            "names": [file.name for file in files],
            "uploaded": [isinstance(file, InputFile) for file in files],
            **kwargs,
        }
        return [
            SimpleNamespace(
                id=20, date=datetime.now(UTC), message="", out=True, sender_id=1, media=None
            )
        ]

    async def send_message(self, _peer: object, text: str, **kwargs: object) -> object:
        self.text_args = {"text": text, **kwargs}
        return SimpleNamespace(
            id=21, date=datetime.now(UTC), message=text, out=True, sender_id=1, media=None
        )


@pytest.mark.asyncio
async def test_send_uses_parse_mode_none_and_file_backed_named_streams(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    client = _SendClient()
    monkeypatch.setattr(_chat_writes, "get_client", lambda _account_id: _async_result(client))
    uploads = []
    for filename in ("photo.jpg", "clip.mp4", "notes.txt"):
        path = tmp_path / "staged.bin"
        path.write_bytes(filename.encode())
        uploads.append(ChatUpload(path=path, file_name=filename))

    sent = await _chat_writes.send_message(
        "acc-1",
        "user",
        3,
        text="*plain operator text*",
        files=uploads,
    )
    assert len(sent) == 1
    assert client.file_args is not None
    assert client.file_args["names"] == ["photo.jpg", "clip.mp4", "notes.txt"]
    assert client.upload_names == ["photo.jpg", "clip.mp4", "notes.txt"]
    assert client.file_args["uploaded"] == [True, True, True]
    assert client.file_args["caption"] == ["*plain operator text*", "", ""]
    assert client.file_args["parse_mode"] is None
    _photo_attrs, photo_mime = utils.get_attributes(InputFile(1, 1, "photo.jpg", ""))
    video_attrs, video_mime = utils.get_attributes(InputFile(1, 1, "clip.mp4", ""))
    document_attrs, document_mime = utils.get_attributes(InputFile(1, 1, "notes.txt", ""))
    assert utils.is_image(InputFile(1, 1, "photo.jpg", "")) is True
    assert photo_mime == "image/jpeg"
    assert any(isinstance(item, DocumentAttributeVideo) for item in video_attrs)
    assert video_mime == "video/mp4"
    assert any(isinstance(item, DocumentAttributeFilename) for item in document_attrs)
    assert document_mime == "text/plain"

    await _chat_writes.send_message("acc-1", "user", 3, text="[literal]", files=[])
    assert client.text_args == {"text": "[literal]", "parse_mode": None}
