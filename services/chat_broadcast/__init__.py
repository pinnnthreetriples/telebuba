"""Chat broadcast («Рассылка по чатам»): accounts write a chain of messages into groups.

The package is the public surface — re-exports only.
"""

from __future__ import annotations

from services.chat_broadcast._errors import (
    ChatBroadcastConflictError,
    ChatBroadcastInvalidError,
    ChatBroadcastRefusedError,
)
from services.chat_broadcast._runtime import (
    reconcile_chat_broadcast_on_startup,
    shutdown_chat_broadcast_on_shutdown,
    start_campaign,
    stop_campaign,
)
from services.chat_broadcast.actions import act_on_target
from services.chat_broadcast.board import load_board
from services.chat_broadcast.campaigns import (
    create_campaign,
    delete_campaign,
    list_campaigns,
    load_settings,
    save_settings,
)
from services.chat_broadcast.media import upload_photo
from services.chat_broadcast.targets import own_chats, resolve

__all__ = [
    "ChatBroadcastConflictError",
    "ChatBroadcastInvalidError",
    "ChatBroadcastRefusedError",
    "act_on_target",
    "create_campaign",
    "delete_campaign",
    "list_campaigns",
    "load_board",
    "load_settings",
    "own_chats",
    "reconcile_chat_broadcast_on_startup",
    "resolve",
    "save_settings",
    "shutdown_chat_broadcast_on_shutdown",
    "start_campaign",
    "stop_campaign",
    "upload_photo",
]
