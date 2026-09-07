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
from core.telegram_client._warm_browse import (
    browse_stickers,
    get_dialogs,
    inline_query,
    link_preview,
    saved_gifs,
    search_messages,
)
from core.telegram_client._warm_chats import mute_peer, toggle_archive
from core.telegram_client._warm_media import vote_in_poll
from core.telegram_client._warm_saved import forward_to_saved, save_draft, self_note
from schemas.telegram_actions import (
    WarmBrowseStickers,
    WarmCheckSettings,
    WarmForwardToSaved,
    WarmGetDialogs,
    WarmInlineQuery,
    WarmLinkPreview,
    WarmMutePeer,
    WarmReadContacts,
    WarmReadNotifySettings,
    WarmSavedGifs,
    WarmSaveDraft,
    WarmSearchMessages,
    WarmSelfNote,
    WarmToggleArchive,
    WarmViewProfile,
    WarmVoteInPoll,
)

if TYPE_CHECKING:
    from telethon import TelegramClient

    from core.telegram_client._action_results import _DispatchResult
    from schemas.telegram_actions import TelegramAction


async def dispatch_warming_action(  # noqa: C901, PLR0911, PLR0912 - one arm per model, as _dispatch_action
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
        case WarmSearchMessages():
            return await search_messages(client, action)
        case WarmLinkPreview():
            return await link_preview(client, action)
        case WarmBrowseStickers():
            return await browse_stickers(client)
        case WarmSavedGifs():
            return await saved_gifs(client)
        case WarmInlineQuery():
            return await inline_query(client, action)
        case WarmSelfNote():
            return await self_note(client, action)
        case WarmSaveDraft():
            return await save_draft(client, action)
        case WarmForwardToSaved():
            return await forward_to_saved(client, action)
        case WarmVoteInPoll():
            return await vote_in_poll(client, action)
        case WarmToggleArchive():
            return await toggle_archive(client, action)
        case WarmMutePeer():
            return await mute_peer(client, action)
        case _:
            msg = f"Unsupported warming action_type: {action.action_type}"
            raise ValueError(msg)


def warm_log_extra(action: TelegramAction) -> dict[str, object]:  # noqa: C901, PLR0911 - one arm per model
    """Static log fields — counts, kinds, channel handles and bot names; never text or URLs."""
    match action:
        case WarmGetDialogs():
            return {"limit": action.limit}
        case WarmViewProfile():
            return {"kind": action.kind, "channel": action.channel, "bot": action.bot}
        case WarmSearchMessages():
            return {"channel": action.channel, "global": action.global_search}
        case WarmLinkPreview():
            return {"channel": action.channel}
        case WarmInlineQuery():
            return {"bot": action.bot}
        case WarmSelfNote():
            return {"scheduled": action.schedule_in_hours is not None}
        case WarmSaveDraft():
            return {"clear": action.text == ""}
        case WarmForwardToSaved():
            return {"channel": action.channel, "source_id": action.message_id}
        case WarmVoteInPoll():
            return {"channel": action.channel}
        case WarmToggleArchive():
            return {"channel": action.channel, "archived": action.archived}
        case WarmMutePeer():
            return {"channel": action.channel, "mute_hours": action.mute_hours}
        case _:
            return {}
