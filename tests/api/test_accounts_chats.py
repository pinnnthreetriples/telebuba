from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx
import pytest

from schemas.chats import ChatMessage, ChatReadResult, ChatSendResult, ChatUpload
from services.accounts.chats import ChatMediaDownload

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    )


def _message(message_id: int = 22) -> ChatMessage:
    return ChatMessage(
        message_id=message_id,
        text="hello",
        date=datetime(2026, 1, 1, tzinfo=UTC),
        outgoing=True,
    )


def test_chat_routes_and_multipart_contract_are_in_openapi(app: FastAPI) -> None:
    schema = app.openapi()
    paths = schema["paths"]
    assert "/api/v1/accounts/{account_id}/chats" in paths
    assert "/api/v1/accounts/{account_id}/chats/{peer_type}/{peer_id}/messages" in paths
    send = paths["/api/v1/accounts/{account_id}/chats/{peer_type}/{peer_id}/messages"]["post"]
    assert send["operationId"] == "sendAccountChatMessage"
    assert "multipart/form-data" in send["requestBody"]["content"]
    media = paths[
        "/api/v1/accounts/{account_id}/chats/{peer_type}/{peer_id}/messages/"
        "{message_id}/media/{media_index}"
    ]["get"]
    assert {item["name"] for item in media["parameters"]} >= {"message_id", "media_index"}


@pytest.mark.asyncio
async def test_send_chat_message_stages_repeated_files_and_preserves_names(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    async def _send(  # noqa: PLR0913
        account_id: str,
        peer_type: str,
        peer_id: str,
        *,
        text: str | None,
        files: list[ChatUpload],
        reply_to: int | None,
    ) -> ChatSendResult:
        seen.update(
            account_id=account_id,
            peer_type=peer_type,
            peer_id=peer_id,
            text=text,
            reply_to=reply_to,
        )
        seen["files"] = [(upload.path.exists(), upload.file_name) for upload in files]
        return ChatSendResult(items=[_message(22), _message(23)])

    monkeypatch.setattr("services.accounts.send_chat_message", _send)
    async with _client(app) as client:
        response = await client.post(
            "/api/v1/accounts/acc-1/chats/chat/42/messages",
            data={"text": "caption", "reply_to": "9"},
            files=[
                ("files", ("photo.jpg", b"photo", "image/jpeg")),
                ("files", ("clip.mp4", b"video", "video/mp4")),
            ],
        )

    assert response.status_code == 200
    assert len(response.json()["items"]) == 2
    assert seen == {
        "account_id": "acc-1",
        "peer_type": "chat",
        "peer_id": "42",
        "text": "caption",
        "reply_to": 9,
        "files": [(True, "photo.jpg"), (True, "clip.mp4")],
    }


@pytest.mark.asyncio
async def test_chat_media_is_streamed_inline_with_nosniff(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _chunks() -> AsyncIterator[bytes]:
        yield b"image"

    async def _download(*_args: object) -> ChatMediaDownload:
        return ChatMediaDownload("pic.jpg", "image/jpeg", 5, _chunks())

    monkeypatch.setattr("services.accounts.get_chat_media_download", _download)
    async with _client(app) as client:
        response = await client.get(
            "/api/v1/accounts/acc-1/chats/user/7/messages/22/media/0",
        )

    assert response.status_code == 200
    assert response.content == b"image"
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"].startswith("inline;")


@pytest.mark.asyncio
async def test_mark_chat_read_returns_acknowledged_max_message_id(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _mark(
        account_id: str, peer_type: str, peer_id: str, max_message_id: int
    ) -> ChatReadResult:
        assert (account_id, peer_type, peer_id, max_message_id) == ("acc-1", "user", "7", 33)
        return ChatReadResult(acknowledged_up_to=max_message_id)

    monkeypatch.setattr("services.accounts.mark_chat_read", _mark)
    async with _client(app) as client:
        response = await client.post(
            "/api/v1/accounts/acc-1/chats/user/7/read",
            json={"max_message_id": 33},
        )
    assert response.status_code == 200
    assert response.json() == {"acknowledged_up_to": 33}
