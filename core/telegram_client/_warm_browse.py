"""Warming reads that browse content the way an opened app does (``warm_get_dialogs``)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.types import InputPeerEmpty

from core.telegram_client._action_results import _DispatchResult

if TYPE_CHECKING:
    from telethon import TelegramClient

    from schemas.telegram_actions import WarmGetDialogs


async def get_dialogs(client: TelegramClient, action: WarmGetDialogs) -> _DispatchResult:
    """Open the chat list from the top; only the count is kept, never the peers."""
    result = await client(
        GetDialogsRequest(
            offset_date=None,
            offset_id=0,
            offset_peer=InputPeerEmpty(),
            limit=action.limit,
            hash=0,
        ),
    )
    # ``DialogsNotModified`` carries no list — it reads as zero dialogs.
    return _DispatchResult(log_extra={"dialogs": len(getattr(result, "dialogs", ()))})
