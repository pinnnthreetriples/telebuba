"""Account chat list, history, read, send and download endpoints."""

from __future__ import annotations

from contextlib import AsyncExitStack
from pathlib import PurePath
from typing import TYPE_CHECKING, Annotated, NoReturn
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Path, Query, UploadFile
from fastapi import status as http_status
from fastapi.responses import StreamingResponse

from api.errors import error_responses
from api.v1._uploads import staged_upload
from core.config import settings
from schemas.api import Page
from schemas.chats import (
    ChatDialog,
    ChatHistoryPage,
    ChatPeerType,
    ChatReadRequest,
    ChatReadResult,
    ChatSendResult,
    ChatUpload,
)
from services import accounts

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

chats_router = APIRouter()
_MAX_CHAT_FILES = 10
_INLINE_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "video/mp4",
    "video/webm",
    "audio/mpeg",
    "audio/mp4",
    "audio/ogg",
    "audio/webm",
    "audio/wav",
    "audio/x-wav",
}


def _read_error(exc: accounts.ChatServiceError) -> NoReturn:
    if exc.code.endswith("invalid") or exc.code == "chat_message_id_invalid":
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=exc.code)
    if exc.code in {
        "chat_not_found",
        "chat_message_not_found",
        "chat_media_not_found",
    }:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=exc.code)
    raise HTTPException(status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE, detail=exc.code)


def _send_error(exc: accounts.ChatServiceError) -> NoReturn:
    if exc.code.endswith("invalid") or exc.code == "chat_message_empty":
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=exc.code)
    if exc.code == "chat_write_forbidden":
        raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN, detail=exc.code)
    if exc.code == "chat_flood_wait":
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=exc.code)
    if exc.code in {"chat_not_found", "chat_message_not_found", "chat_media_not_found"}:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=exc.code)
    raise HTTPException(status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE, detail=exc.code)


async def _read_call[**P, R](
    function: Callable[P, Awaitable[R]], *args: P.args, **kwargs: P.kwargs
) -> R:
    try:
        return await function(*args, **kwargs)
    except accounts.AccountNotFoundError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail="account_not_found"
        ) from exc
    except accounts.ChatServiceError as exc:
        _read_error(exc)


async def _send_call[**P, R](
    function: Callable[P, Awaitable[R]], *args: P.args, **kwargs: P.kwargs
) -> R:
    try:
        return await function(*args, **kwargs)
    except accounts.AccountNotFoundError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail="account_not_found"
        ) from exc
    except accounts.ChatServiceError as exc:
        _send_error(exc)


@chats_router.get(
    "/accounts/{account_id}/chats",
    response_model=Page[ChatDialog],
    operation_id="listAccountChats",
    responses=error_responses(400, 404, 503),
)
async def list_account_chats(
    account_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: str | None = None,
) -> Page[ChatDialog]:
    return await _read_call(accounts.list_account_chats, account_id, limit=limit, cursor=cursor)


@chats_router.get(
    "/accounts/{account_id}/chats/{peer_type}/{peer_id}/messages",
    response_model=ChatHistoryPage,
    operation_id="listAccountChatMessages",
    responses=error_responses(400, 404, 503),
)
async def list_account_chat_messages(
    account_id: str,
    peer_type: ChatPeerType,
    peer_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    before_id: Annotated[int | None, Query(ge=1)] = None,
) -> ChatHistoryPage:
    return await _read_call(
        accounts.list_chat_history,
        account_id,
        peer_type,
        peer_id,
        limit=limit,
        before_id=before_id,
    )


@chats_router.post(
    "/accounts/{account_id}/chats/{peer_type}/{peer_id}/read",
    response_model=ChatReadResult,
    operation_id="markAccountChatRead",
    responses=error_responses(400, 404, 503),
)
async def mark_account_chat_read(
    account_id: str,
    peer_type: ChatPeerType,
    peer_id: str,
    body: ChatReadRequest,
) -> ChatReadResult:
    return await _read_call(
        accounts.mark_chat_read,
        account_id,
        peer_type,
        peer_id,
        body.max_message_id,
    )


@chats_router.post(
    "/accounts/{account_id}/chats/{peer_type}/{peer_id}/messages",
    response_model=ChatSendResult,
    operation_id="sendAccountChatMessage",
    responses=error_responses(400, 403, 404, 409, 413, 503),
)
async def send_account_chat_message(  # noqa: PLR0913
    account_id: str,
    peer_type: ChatPeerType,
    peer_id: str,
    text: Annotated[str | None, Form()] = None,
    reply_to: Annotated[int | None, Form(ge=1)] = None,
    files: Annotated[list[UploadFile] | None, File()] = None,
) -> ChatSendResult:
    uploads = files or []
    if len(uploads) > _MAX_CHAT_FILES:
        raise HTTPException(
            status_code=http_status.HTTP_413_CONTENT_TOO_LARGE, detail="too_many_files"
        )
    staged_files: list[ChatUpload] = []
    async with AsyncExitStack() as stack:
        for file in uploads:
            if file.size is not None and file.size > settings.chats.media_max_bytes:
                raise HTTPException(
                    status_code=http_status.HTTP_413_CONTENT_TOO_LARGE,
                    detail="chat_media_too_large",
                )
            filename = (file.filename or "attachment").replace("\\", "/").split("/")[-1]
            filename = "".join(
                char for char in filename if char.isprintable() and char not in '";'
            )[:180]
            path = await stack.enter_async_context(
                staged_upload(
                    file,
                    max_bytes=settings.chats.media_max_bytes,
                    detail="chat_media_too_large",
                    status_code=http_status.HTTP_413_CONTENT_TOO_LARGE,
                    suffix=PurePath(filename).suffix[:12],
                )
            )
            staged_files.append(ChatUpload(path=path, file_name=filename or "attachment"))
        return await _send_call(
            accounts.send_chat_message,
            account_id,
            peer_type,
            peer_id,
            text=text,
            files=staged_files,
            reply_to=reply_to,
        )


@chats_router.get(
    "/accounts/{account_id}/chats/{peer_type}/{peer_id}/messages/{message_id}/media/{media_index}",
    operation_id="getAccountChatMedia",
    responses=error_responses(400, 404, 503),
)
async def get_account_chat_media(
    account_id: str,
    peer_type: ChatPeerType,
    peer_id: str,
    message_id: int,
    media_index: Annotated[int, Path(ge=0)],
) -> StreamingResponse:
    media = await _read_call(
        accounts.get_chat_media_download,
        account_id,
        peer_type,
        peer_id,
        message_id,
        media_index,
    )
    mime_type = (
        media.mime_type if media.mime_type in _INLINE_MIME_TYPES else "application/octet-stream"
    )
    disposition = "inline" if mime_type in _INLINE_MIME_TYPES else "attachment"
    filename = quote(media.filename or f"telegram-{message_id}", safe="")
    headers = {
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{filename}",
        "X-Content-Type-Options": "nosniff",
    }
    return StreamingResponse(media.chunks, media_type=mime_type, headers=headers)
