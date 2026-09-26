"""Shared typed helpers and error translation for pooled chat operations."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from telethon import errors
from telethon.tl.types import (
    DocumentAttributeAnimated,
    DocumentAttributeAudio,
    DocumentAttributeSticker,
    DocumentAttributeVideo,
    MessageMediaDocument,
    MessageMediaPhoto,
    PeerChannel,
    PeerChat,
    PeerUser,
)

from core.telegram_client._pool import (
    TelegramClientPoolError,
    TelegramClientUnavailableError,
)
from core.telegram_client._profile import _DEAD_SESSION_ERRORS
from schemas.chats import ChatMedia, ChatMediaKind, ChatMessage, ChatPeerType

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from telethon import TelegramClient, hints


_PeerRef = PeerUser | PeerChat | PeerChannel


class ChatGatewayError(RuntimeError):
    """Stable, safe error code from a chat gateway operation."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _peer_ref(peer_type: ChatPeerType, peer_id: int) -> _PeerRef:
    if peer_type == "user":
        return PeerUser(user_id=peer_id)
    if peer_type == "chat":
        return PeerChat(chat_id=peer_id)
    return PeerChannel(channel_id=peer_id)


async def _entity(
    client: TelegramClient, peer_type: ChatPeerType, peer_id: int
) -> tuple[hints.EntityLike, Any]:
    try:
        peer = await client.get_input_entity(_peer_ref(peer_type, peer_id))
        return peer, await client.get_entity(peer)
    except (ValueError, errors.PeerIdInvalidError, errors.ChannelPrivateError) as exc:
        raise ChatGatewayError("chat_not_found") from exc  # noqa: EM101


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
            raise ChatGatewayError("chat_flood_wait") from exc  # noqa: EM101
        except _DEAD_SESSION_ERRORS as exc:
            raise ChatGatewayError("chat_session_unavailable") from exc  # noqa: EM101
        except (
            TelegramClientUnavailableError,
            TelegramClientPoolError,
            ConnectionError,
            TimeoutError,
            OSError,
        ) as exc:
            raise ChatGatewayError("chat_account_unavailable") from exc  # noqa: EM101
        except errors.RPCError as exc:
            raise ChatGatewayError("chat_telegram_error") from exc  # noqa: EM101

    return wrapped
