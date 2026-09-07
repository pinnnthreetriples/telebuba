"""Dispatcher for the ``warm_*`` family — routes each model to its focused sibling module.

Matches on the concrete model (that is what narrows the type inside the arm) and
raises for anything unhandled: a forgotten arm must not fall through silently.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.telegram_client._warm_account import (
    check_settings,
    read_contacts,
    read_notify_settings,
    view_profile,
)
from core.telegram_client._warm_browse import get_dialogs
from schemas.telegram_actions import (
    WarmCheckSettings,
    WarmGetDialogs,
    WarmReadContacts,
    WarmReadNotifySettings,
    WarmViewProfile,
)

if TYPE_CHECKING:
    from telethon import TelegramClient

    from core.telegram_client._action_results import _DispatchResult
    from schemas.telegram_actions import TelegramAction


async def dispatch_warming_action(
    client: TelegramClient,
    action: TelegramAction,
) -> _DispatchResult:
    match action:
        case WarmGetDialogs():
            return await get_dialogs(client, action)
        case WarmReadContacts():
            return await read_contacts(client)
        case WarmReadNotifySettings():
            return await read_notify_settings(client)
        case WarmCheckSettings():
            return await check_settings(client, action)
        case WarmViewProfile():
            return await view_profile(client, action)
        case _:
            msg = f"Unsupported warming action_type: {action.action_type}"
            raise ValueError(msg)


def warm_log_extra(action: TelegramAction) -> dict[str, object]:
    """Static log fields — counts, kinds and channel handles only; never text or URLs."""
    match action:
        case WarmGetDialogs():
            return {"limit": action.limit}
        case WarmViewProfile():
            return {"kind": action.kind, "channel": action.channel}
        case _:
            return {}
