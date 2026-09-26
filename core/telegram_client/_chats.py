"""Typed Telegram chat operations that need the pooled client directly.

The message download path is an async stream, so it cannot use the ordinary
materialized ``ActionResult`` dispatcher. All Telethon objects remain here.
"""

from __future__ import annotations

import base64
import binascii
import json
import mimetypes
from contextlib import ExitStack
from datetime import datetime
from typing import TYPE_CHECKING, Any, BinaryIO, Protocol, Self, cast

from telethon import errors
from telethon.tl.types import (
    Channel,
    Chat,
    DocumentAttributeAnimated,
    DocumentAttributeAudio,
    DocumentAttributeSticker,
    DocumentAttributeVideo,
    MessageMediaDocument,
    MessageMediaPhoto,
    PeerChannel,
    PeerChat,
    PeerUser,
    User,
)

from core.telegram_client._pool import (
    TelegramClientPoolError,
    TelegramClientUnavailableError,
    get_client,
)
from core.telegram_client._profile import _DEAD_SESSION_ERRORS
from schemas.chats import (
    ChatDialog,
    ChatMedia,
    ChatMediaKind,
    ChatMessage,
    ChatPeerType,
    ChatUpload,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
    from pathlib import Path

    from telethon import TelegramClient, hints


_PeerRef = PeerUser | PeerChat | PeerChannel


class _Dialog(Protocol):
    entity: User | Chat | Channel
    message: object | None
    title: str | None
    folder_id: int | None
    unread_count: int


class _PermissionClient(Protocol):
    async def get_permissions(self, entity: hints.EntityLike, user: str) -> object: ...


class ChatGatewayError(RuntimeError):
    """Stable, safe error code from a chat gateway operation."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


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


def _chat_error(code: str) -> ChatGatewayError:
    return ChatGatewayError(code)


def _peer_ref(peer_type: ChatPeerType, peer_id: int) -> _PeerRef:
    if peer_type == "user":
        return PeerUser(user_id=peer_id)
    if peer_type == "chat":
        return PeerChat(chat_id=peer_id)
    return PeerChannel(channel_id=peer_id)


def _peer_type(entity: object) -> ChatPeerType:
    if isinstance(entity, User):
        return "user"
    if isinstance(entity, Chat):
        return "chat"
    if isinstance(entity, Channel):
        return "channel"
    raise _chat_error("chat_peer_unsupported")  # noqa: EM101


def _cursor_encode(data: dict[str, object]) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _cursor_decode(cursor: str | None) -> dict[str, object] | None:
    if cursor is None:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        decoded = json.loads(raw)
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as exc:
        raise _chat_error("chat_cursor_invalid") from exc  # noqa: EM101
    if not isinstance(decoded, dict) or decoded.get("peer_type") not in {"user", "chat", "channel"}:
        raise _chat_error("chat_cursor_invalid")  # noqa: EM101
    return cast("dict[str, object]", decoded)


def _media_kind(message: object) -> ChatMediaKind | None:  # noqa: PLR0911
    media = getattr(message, "media", None)
    if isinstance(media, MessageMediaPhoto):
        return "image"
    if not isinstance(media, MessageMediaDocument):
        return None
    document = media.document
    if document is None:
        return "unknown"
    attributes = getattr(document, "attributes", ())
    if any(isinstance(item, DocumentAttributeSticker) for item in attributes):
        return "sticker"
    if any(isinstance(item, DocumentAttributeVideo) and item.round_message for item in attributes):
        return "video_note"
    audio = next((item for item in attributes if isinstance(item, DocumentAttributeAudio)), None)
    if audio is not None:
        return "voice" if audio.voice else "audio"
    if any(isinstance(item, DocumentAttributeAnimated) for item in attributes):
        return "animation"
    mime_type = str(getattr(document, "mime_type", "") or "")
    if any(isinstance(item, DocumentAttributeVideo) for item in attributes) or mime_type.startswith(
        "video/"
    ):
        return "video"
    if mime_type.startswith("image/"):
        return "image"
    return "document"


def _media(
    message: object, account_id: str, peer_type: ChatPeerType, peer_id: int
) -> list[ChatMedia]:
    kind = _media_kind(message)
    if kind is None:
        return []
    file = getattr(message, "file", None)
    return [
        ChatMedia(
            kind=kind,
            file_name=getattr(file, "name", None),
            mime_type=getattr(file, "mime_type", None),
            size=getattr(file, "size", None),
            download_url=(
                f"/api/v1/accounts/{account_id}/chats/{peer_type}/{peer_id}/messages/"
                f"{getattr(message, 'id', 0)}/media/0"
            ),
        )
    ]


def _message(
    message: object, account_id: str, peer_type: ChatPeerType, peer_id: int
) -> ChatMessage:
    date = getattr(message, "date", None) or datetime.now().astimezone()
    return ChatMessage(
        message_id=int(getattr(message, "id", 0)),
        text=str(getattr(message, "message", None) or ""),
        date=date,
        outgoing=getattr(message, "out", False) is True,
        sender_id=getattr(message, "sender_id", None)
        if isinstance(getattr(message, "sender_id", None), int)
        else None,
        media=_media(message, account_id, peer_type, peer_id),
    )


def _dialog(dialog: _Dialog, account_id: str) -> tuple[ChatDialog, object | None]:
    entity = dialog.entity
    peer_type = _peer_type(entity)
    peer_id = int(entity.id)
    last = getattr(dialog, "message", None)
    last_message = _message(last, account_id, peer_type, peer_id) if last is not None else None
    return (
        ChatDialog(
            peer_type=peer_type,
            peer_id=str(peer_id),
            title=str(
                getattr(dialog, "title", None) or getattr(entity, "first_name", None) or peer_id
            ),
            username=getattr(entity, "username", None),
            is_archived=getattr(dialog, "folder_id", None) == 1,
            unread_count=int(getattr(dialog, "unread_count", 0) or 0),
            last_message=last_message,
        ),
        last,
    )


def _next_dialog_cursor(dialogs: Sequence[_Dialog], limit: int) -> str | None:
    if len(dialogs) <= limit:
        return None
    page = dialogs[:limit]
    last_dialog = page[-1]
    last_entity = last_dialog.entity
    last_message = next(
        (
            getattr(item, "message", None)
            for item in reversed(page)
            if getattr(item, "message", None) is not None
        ),
        None,
    )
    return _cursor_encode(
        {
            "peer_type": _peer_type(last_entity),
            "peer_id": str(last_entity.id),
            "message_id": int(last_message.id) if last_message is not None else 0,
            "date": last_message.date.isoformat() if last_message is not None else None,
        }
    )


def _safe_filename(value: str | None) -> str | None:
    if value is None:
        return None
    name = value.replace("\\", "/").split("/")[-1]
    name = "".join(char for char in name if char.isprintable() and char not in '";')
    return name[:180] or None


def _translate_errors[**P, R](
    function: Callable[P, Awaitable[R]],
) -> Callable[P, Awaitable[R]]:
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await function(*args, **kwargs)
        except ChatGatewayError:
            raise
        except (
            errors.FloodWaitError,
            errors.SlowModeWaitError,
            errors.FloodPremiumWaitError,
            errors.PeerFloodError,
        ) as exc:
            raise _chat_error("chat_flood_wait") from exc  # noqa: EM101
        except _DEAD_SESSION_ERRORS as exc:
            raise _chat_error("chat_session_unavailable") from exc  # noqa: EM101
        except (
            TelegramClientUnavailableError,
            TelegramClientPoolError,
            ConnectionError,
            TimeoutError,
            OSError,
        ) as exc:
            raise _chat_error("chat_account_unavailable") from exc  # noqa: EM101
        except errors.RPCError as exc:
            raise _chat_error("chat_telegram_error") from exc  # noqa: EM101

    return wrapped


@_translate_errors
async def list_dialogs(
    account_id: str, *, limit: int, cursor: str | None
) -> tuple[list[ChatDialog], str | None]:
    client = await get_client(account_id)
    data = _cursor_decode(cursor)
    offset_peer: hints.EntityLike | None = None
    message_id = 0
    offset_date: datetime | None = None
    if data is not None:
        try:
            peer_type = cast("ChatPeerType", data["peer_type"])
            peer_id_value = data.get("peer_id")
            message_id_value = data.get("message_id") or 0
            if not isinstance(peer_id_value, (str, int)) or not isinstance(
                message_id_value, (str, int)
            ):
                raise _chat_error("chat_cursor_invalid")  # noqa: EM101
            peer_id = int(peer_id_value)
            message_id = int(message_id_value)
            date_value = data.get("date")
            if peer_id < 1 or message_id < 0:
                raise _chat_error("chat_cursor_invalid")  # noqa: EM101
            offset_date = datetime.fromisoformat(str(date_value)) if date_value else None
            offset_peer = await client.get_input_entity(_peer_ref(peer_type, peer_id))
        except (ValueError, TypeError, KeyError) as exc:
            raise _chat_error("chat_cursor_invalid") from exc  # noqa: EM101
    try:
        # folder=None is Telethon's all-folders view, including archived dialogs.
        if data is None:
            dialogs = [dialog async for dialog in client.iter_dialogs(limit=limit + 1)]
        else:
            if offset_peer is None:
                raise _chat_error("chat_cursor_invalid")  # noqa: EM101
            dialogs = [
                dialog
                async for dialog in client.iter_dialogs(
                    limit=limit + 1,
                    offset_peer=offset_peer,
                    offset_id=message_id,
                    offset_date=offset_date,
                    ignore_pinned=True,
                )
            ]
    except (errors.ChannelPrivateError, errors.PeerIdInvalidError) as exc:
        raise _chat_error("chat_list_unavailable") from exc  # noqa: EM101
    next_cursor = _next_dialog_cursor(dialogs, limit)
    output = [_dialog(dialog, account_id)[0] for dialog in dialogs[:limit]]
    return output, next_cursor


async def _entity(
    client: TelegramClient, peer_type: ChatPeerType, peer_id: int
) -> tuple[hints.EntityLike, Any]:
    try:
        peer = await client.get_input_entity(_peer_ref(peer_type, peer_id))
        return peer, await client.get_entity(peer)
    except (ValueError, errors.PeerIdInvalidError, errors.ChannelPrivateError) as exc:
        raise _chat_error("chat_not_found") from exc  # noqa: EM101


@_translate_errors
async def read_history(
    account_id: str, peer_type: ChatPeerType, peer_id: int, *, limit: int, before_id: int | None
) -> tuple[list[ChatMessage], int | None]:
    client = await get_client(account_id)
    peer, _ = await _entity(client, peer_type, peer_id)
    try:
        # Telethon's overload also permits a single Message/None, but `limit`
        # always yields a TotalList for this call.
        messages = cast(
            "Sequence[object]",
            await client.get_messages(peer, limit=limit + 1, max_id=before_id or 0),
        )
    except (errors.ChannelPrivateError, errors.PeerIdInvalidError) as exc:
        raise _chat_error("chat_history_unavailable") from exc  # noqa: EM101
    has_more = len(messages) > limit
    messages = list(reversed(messages[:limit]))
    results = [
        _message(item, account_id, peer_type, peer_id) for item in messages if item is not None
    ]
    return results, (results[0].message_id if has_more and results else None)


@_translate_errors
async def mark_read(
    account_id: str, peer_type: ChatPeerType, peer_id: int, max_message_id: int
) -> None:
    client = await get_client(account_id)
    peer, _ = await _entity(client, peer_type, peer_id)
    try:
        await client.send_read_acknowledge(peer, max_id=max_message_id)
    except (errors.ChannelPrivateError, errors.PeerIdInvalidError) as exc:
        raise _chat_error("chat_read_unavailable") from exc  # noqa: EM101


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
        raise _chat_error("chat_write_forbidden")  # noqa: EM101


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
        raise _chat_error("chat_message_empty")  # noqa: EM101
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
        raise _chat_error("chat_write_forbidden") from exc  # noqa: EM101
    except errors.FloodWaitError as exc:
        raise _chat_error("chat_flood_wait") from exc  # noqa: EM101
    except (errors.ChannelPrivateError, errors.PeerIdInvalidError, ValueError) as exc:
        raise _chat_error("chat_not_found") from exc  # noqa: EM101
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
        raise _chat_error("chat_message_not_found") from exc  # noqa: EM101
    if message is None or getattr(message, "media", None) is None or media_index != 0:
        raise _chat_error("chat_media_not_found")  # noqa: EM101
    kind = _media_kind(message)
    if kind is None:
        raise _chat_error("chat_media_not_found")  # noqa: EM101
    file = getattr(message, "file", None)
    filename = _safe_filename(getattr(file, "name", None))
    mime = getattr(file, "mime_type", None) or (mimetypes.guess_type(filename or "")[0])
    size = getattr(file, "size", None)
    media = getattr(message, "media", None)
    source = getattr(media, "photo", None) or getattr(media, "document", None)
    if source is None:
        raise _chat_error("chat_media_not_found")  # noqa: EM101

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
