"""Warming writes on a joined channel's dialog: archive / unarchive it, mute / unmute it.

Both are what a real user does to a channel that posts too much, both are reversible
and neither touches content. Log rows carry the handle and the flag or hours only.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from telethon import errors
from telethon.tl.functions.account import UpdateNotifySettingsRequest
from telethon.tl.functions.folders import EditPeerFoldersRequest
from telethon.tl.types import InputFolderPeer, InputNotifyPeer, InputPeerNotifySettings

from core.telegram_client._action_results import _DispatchResult
from core.telegram_client._media import ProfileGatewayError

if TYPE_CHECKING:
    from telethon import TelegramClient

    from schemas.telegram_actions import WarmMutePeer, WarmToggleArchive

# Telegram's two built-in folders; the archive is folder 1.
_ARCHIVE_FOLDER = 1
_MAIN_FOLDER = 0
# On the wire ``mute_until=0`` means "unmuted"; omitting the field means "leave as is".
_UNMUTED = 0


async def toggle_archive(client: TelegramClient, action: WarmToggleArchive) -> _DispatchResult:
    """Move the channel into the archive folder (``archived=True``) or back to the main list."""
    peer = await client.get_input_entity(action.channel)
    folder_id = _ARCHIVE_FOLDER if action.archived else _MAIN_FOLDER
    try:
        await client(
            EditPeerFoldersRequest(folder_peers=[InputFolderPeer(peer=peer, folder_id=folder_id)]),
        )
    except errors.FolderIdInvalidError as exc:
        raise ProfileGatewayError("bad_folder") from exc  # noqa: EM101
    return _DispatchResult(log_extra={"archived": action.archived})


async def mute_peer(client: TelegramClient, action: WarmMutePeer) -> _DispatchResult:
    """Mute the channel for ``mute_hours`` (``0`` unmutes); never "forever"."""
    peer = await client.get_input_entity(action.channel)
    mute_until: datetime | int = _UNMUTED
    if action.mute_hours:
        # Whole minutes: the client's picker only offers those, so seconds would fingerprint.
        mute_until = (datetime.now(UTC) + timedelta(hours=action.mute_hours)).replace(
            second=0, microsecond=0
        )
    try:
        await client(
            UpdateNotifySettingsRequest(
                peer=InputNotifyPeer(peer=peer),
                settings=InputPeerNotifySettings(mute_until=mute_until),  # ty: ignore[invalid-argument-type]
            ),
        )
    except errors.SettingsInvalidError as exc:
        raise ProfileGatewayError("bad_settings") from exc  # noqa: EM101
    return _DispatchResult(log_extra={"mute_hours": action.mute_hours})
