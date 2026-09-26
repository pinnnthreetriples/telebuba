"""Account chat operations and stable service errors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.telegram_client import _chats as chat_gateway
from schemas.api import Page
from schemas.chats import (
    ChatDialog,
    ChatHistoryPage,
    ChatPeerType,
    ChatReadResult,
    ChatSendResult,
    ChatUpload,
)
from services.accounts.lifecycle import require_account

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence


class ChatServiceError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ChatMediaDownload:
    filename: str | None
    mime_type: str | None
    size: int | None
    chunks: AsyncIterator[bytes]


def _peer_id(value: str) -> int:
    try:
        peer_id = int(value)
    except ValueError as exc:
        raise ChatServiceError("chat_peer_invalid") from exc  # noqa: EM101
    if peer_id < 1 or peer_id > 2**63 - 1:
        raise ChatServiceError("chat_peer_invalid")  # noqa: EM101
    return peer_id


def _gateway_error(exc: chat_gateway.ChatGatewayError) -> ChatServiceError:
    return ChatServiceError(exc.code)


async def list_account_chats(
    account_id: str, *, limit: int, cursor: str | None
) -> Page[ChatDialog]:
    await require_account(account_id)
    try:
        items, next_cursor = await chat_gateway.list_dialogs(account_id, limit=limit, cursor=cursor)
    except chat_gateway.ChatGatewayError as exc:
        raise _gateway_error(exc) from exc
    return Page(items=items, next_cursor=next_cursor)


async def list_chat_history(
    account_id: str,
    peer_type: ChatPeerType,
    peer_id: str,
    *,
    limit: int,
    before_id: int | None,
) -> ChatHistoryPage:
    await require_account(account_id)
    if before_id is not None and before_id < 1:
        raise ChatServiceError("chat_message_cursor_invalid")  # noqa: EM101
    try:
        items, next_before_id = await chat_gateway.read_history(
            account_id, peer_type, _peer_id(peer_id), limit=limit, before_id=before_id
        )
    except chat_gateway.ChatGatewayError as exc:
        raise _gateway_error(exc) from exc
    return ChatHistoryPage(items=items, next_before_id=next_before_id)


async def mark_chat_read(
    account_id: str, peer_type: ChatPeerType, peer_id: str, max_message_id: int
) -> ChatReadResult:
    await require_account(account_id)
    if max_message_id < 1:
        raise ChatServiceError("chat_message_id_invalid")  # noqa: EM101
    try:
        await chat_gateway.mark_read(account_id, peer_type, _peer_id(peer_id), max_message_id)
    except chat_gateway.ChatGatewayError as exc:
        raise _gateway_error(exc) from exc
    return ChatReadResult(acknowledged_up_to=max_message_id)


async def send_chat_message(  # noqa: PLR0913
    account_id: str,
    peer_type: ChatPeerType,
    peer_id: str,
    *,
    text: str | None,
    files: Sequence[ChatUpload] = (),
    reply_to: int | None = None,
) -> ChatSendResult:
    await require_account(account_id)
    if reply_to is not None and reply_to < 1:
        raise ChatServiceError("chat_message_id_invalid")  # noqa: EM101
    try:
        items = await chat_gateway.send_message(
            account_id,
            peer_type,
            _peer_id(peer_id),
            text=text,
            files=files,
            reply_to=reply_to,
        )
    except chat_gateway.ChatGatewayError as exc:
        raise _gateway_error(exc) from exc
    return ChatSendResult(items=items)


async def get_chat_media_download(
    account_id: str,
    peer_type: ChatPeerType,
    peer_id: str,
    message_id: int,
    media_index: int,
) -> ChatMediaDownload:
    await require_account(account_id)
    if message_id < 1 or media_index < 0:
        raise ChatServiceError("chat_media_not_found")  # noqa: EM101
    try:
        filename, mime_type, size, chunks = await chat_gateway.media_download(
            account_id,
            peer_type,
            _peer_id(peer_id),
            message_id,
            media_index,
        )
    except chat_gateway.ChatGatewayError as exc:
        raise _gateway_error(exc) from exc
    return ChatMediaDownload(filename, mime_type, size, chunks)


__all__ = [
    "ChatMediaDownload",
    "ChatServiceError",
    "get_chat_media_download",
    "list_account_chats",
    "list_chat_history",
    "mark_chat_read",
    "send_chat_message",
]
