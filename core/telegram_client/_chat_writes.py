"""Telegram chat send and streaming media operations."""

from __future__ import annotations

import mimetypes
from contextlib import ExitStack
from typing import TYPE_CHECKING, BinaryIO, Protocol, Self, cast

from telethon import errors

from core.telegram_client._chat_common import (
    ChatGatewayError,
    _entity,
    _media_kind,
    _message,
    _safe_filename,
    _translate_errors,
)
from core.telegram_client._pool import get_client

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from pathlib import Path

    from telethon import hints

    from schemas.chats import ChatMediaKind, ChatMessage, ChatPeerType, ChatUpload


class _PermissionClient(Protocol):
    async def get_permissions(self, entity: hints.EntityLike, user: str) -> object: ...


class _NamedUpload:
    """File-backed stream whose Telegram filename is the sanitized operator filename."""

    def __init__(self, path: Path, name: str) -> None:
        self._stream = path.open("rb")
        self.name = name

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._stream.seek(offset, whence)

    def tell(self) -> int:
        return self._stream.tell()

    def close(self) -> None:
        self._stream.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


async def _ensure_writable(
    client: _PermissionClient, peer_type: ChatPeerType, entity: hints.EntityLike
) -> None:
    if peer_type == "user":
        return
    # A broadcast channel is writable by its creator or an admin with post_messages;
    # generic chat send_messages checks do not represent that right.
    if peer_type == "channel" and getattr(entity, "broadcast", False):
        return
    try:
        permissions = await client.get_permissions(entity, "me")
    except (
        errors.FloodWaitError,
        errors.SlowModeWaitError,
        errors.FloodPremiumWaitError,
        errors.PeerFloodError,
    ):
        raise
    except errors.RPCError:
        # Permission reads can be incomplete for creators/participants; let the actual
        # send RPC make the final decision unless Telegram confirms a ban below.
        return
    banned = getattr(permissions, "banned_rights", None)
    if getattr(banned, "send_messages", False) is True:
        raise ChatGatewayError("chat_write_forbidden")  # noqa: EM101


@_translate_errors
async def send_message(  # noqa: PLR0913
    account_id: str,
    peer_type: ChatPeerType,
    peer_id: int,
    *,
    text: str | None,
    files: Sequence[ChatUpload] = (),
    reply_to: int | None = None,
) -> list[ChatMessage]:
    if not (text and text.strip()) and not files:
        raise ChatGatewayError("chat_message_empty")  # noqa: EM101
    sent_messages: list[object] = []
    client = await get_client(account_id)
    peer, entity = await _entity(client, peer_type, peer_id)
    await _ensure_writable(client, peer_type, entity)
    try:
        if files:
            captions = [text or ""] + [""] * (len(files) - 1)
            with ExitStack() as stack:
                uploaded = []
                for upload in files:
                    staged_stream = stack.enter_context(_NamedUpload(upload.path, upload.file_name))
                    uploaded.append(
                        await client.upload_file(
                            cast(BinaryIO, staged_stream),  # noqa: TC006  # Keep runtime name visible to vulture.
                            file_name=upload.file_name,
                        )
                    )
                if reply_to is None:
                    sent = await client.send_file(peer, uploaded, caption=captions, parse_mode=None)
                else:
                    sent = await client.send_file(
                        peer,
                        uploaded,
                        caption=captions,
                        parse_mode=None,
                        reply_to=reply_to,
                    )
            sent_messages = sent if isinstance(sent, list) else [sent]
        elif text is not None:
            if reply_to is None:
                sent_messages = [await client.send_message(peer, text, parse_mode=None)]
            else:
                sent_messages = [
                    await client.send_message(peer, text, parse_mode=None, reply_to=reply_to)
                ]
    except (
        errors.ChatWriteForbiddenError,
        errors.UserBannedInChannelError,
        errors.UserPrivacyRestrictedError,
    ) as exc:
        raise ChatGatewayError("chat_write_forbidden") from exc  # noqa: EM101
    except errors.FloodWaitError as exc:
        raise ChatGatewayError("chat_flood_wait") from exc  # noqa: EM101
    except (errors.ChannelPrivateError, errors.PeerIdInvalidError, ValueError) as exc:
        raise ChatGatewayError("chat_not_found") from exc  # noqa: EM101
    return [_message(message, account_id, peer_type, peer_id) for message in sent_messages]


@_translate_errors
async def media_download(
    account_id: str, peer_type: ChatPeerType, peer_id: int, message_id: int, media_index: int
) -> tuple[str | None, str | None, int | None, AsyncIterator[bytes]]:
    client = await get_client(account_id)
    peer, _ = await _entity(client, peer_type, peer_id)
    try:
        message = await client.get_messages(peer, ids=message_id)
    except (errors.ChannelPrivateError, errors.PeerIdInvalidError) as exc:
        raise ChatGatewayError("chat_message_not_found") from exc  # noqa: EM101
    if message is None or getattr(message, "media", None) is None or media_index != 0:
        raise ChatGatewayError("chat_media_not_found")  # noqa: EM101
    kind: ChatMediaKind | None = _media_kind(message)
    if kind is None:
        raise ChatGatewayError("chat_media_not_found")  # noqa: EM101
    file = getattr(message, "file", None)
    filename = _safe_filename(getattr(file, "name", None))
    mime = getattr(file, "mime_type", None) or mimetypes.guess_type(filename or "")[0]
    size = getattr(file, "size", None)
    media = getattr(message, "media", None)
    source = getattr(media, "photo", None) or getattr(media, "document", None)
    if source is None:
        raise ChatGatewayError("chat_media_not_found")  # noqa: EM101

    async def chunks() -> AsyncIterator[bytes]:
        stream = client.iter_download(source)
        try:
            async for chunk in stream:
                yield chunk
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                await close()

    return filename, mime, size, chunks()
