"""Typed Telegram chat operations that need the pooled client directly.

The message download path is an async stream, so it cannot use the ordinary
materialized ``ActionResult`` dispatcher. All Telethon objects remain here.
"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime
from typing import TYPE_CHECKING, Protocol, cast

from telethon import errors
from telethon.tl.types import Channel, Chat, User

from core.telegram_client._chat_common import (
    ChatGatewayError,
    _entity,
    _message,
    _peer_ref,
    _translate_errors,
)
from core.telegram_client._chat_writes import media_download, send_message
from core.telegram_client._pool import get_client
from schemas.chats import ChatDialog, ChatMessage, ChatPeerType

if TYPE_CHECKING:
    from collections.abc import Sequence

    from telethon import hints


class _Dialog(Protocol):
    entity: User | Chat | Channel
    message: object | None
    title: str | None
    folder_id: int | None
    unread_count: int


def _peer_type(entity: object) -> ChatPeerType:
    if isinstance(entity, User):
        return "user"
    if isinstance(entity, Chat):
        return "chat"
    if isinstance(entity, Channel):
        return "channel"
    raise ChatGatewayError("chat_peer_unsupported")  # noqa: EM101


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
        raise ChatGatewayError("chat_cursor_invalid") from exc  # noqa: EM101
    if not isinstance(decoded, dict) or decoded.get("peer_type") not in {"user", "chat", "channel"}:
        raise ChatGatewayError("chat_cursor_invalid")  # noqa: EM101
    return cast("dict[str, object]", decoded)


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
                raise ChatGatewayError("chat_cursor_invalid")  # noqa: EM101
            peer_id = int(peer_id_value)
            message_id = int(message_id_value)
            date_value = data.get("date")
            if peer_id < 1 or message_id < 0:
                raise ChatGatewayError("chat_cursor_invalid")  # noqa: EM101
            offset_date = datetime.fromisoformat(str(date_value)) if date_value else None
            offset_peer = await client.get_input_entity(_peer_ref(peer_type, peer_id))
        except (ValueError, TypeError, KeyError) as exc:
            raise ChatGatewayError("chat_cursor_invalid") from exc  # noqa: EM101
    try:
        # folder=None is Telethon's all-folders view, including archived dialogs.
        if data is None:
            dialogs = [dialog async for dialog in client.iter_dialogs(limit=limit + 1)]
        else:
            if offset_peer is None:
                raise ChatGatewayError("chat_cursor_invalid")  # noqa: EM101
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
        raise ChatGatewayError("chat_list_unavailable") from exc  # noqa: EM101
    next_cursor = _next_dialog_cursor(dialogs, limit)
    output = [_dialog(dialog, account_id)[0] for dialog in dialogs[:limit]]
    return output, next_cursor


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
        raise ChatGatewayError("chat_history_unavailable") from exc  # noqa: EM101
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
        raise ChatGatewayError("chat_read_unavailable") from exc  # noqa: EM101


__all__ = [
    "ChatGatewayError",
    "list_dialogs",
    "mark_read",
    "media_download",
    "read_history",
    "send_message",
]
