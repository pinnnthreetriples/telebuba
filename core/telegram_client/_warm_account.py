"""Warming reads of the account's own state: contacts, notifications, settings, profiles.

Every request here is a read a mobile client issues when its owner opens a settings
screen. Nothing is written and no contact is ever imported — the operator's decision.
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from telethon.tl.functions.account import (
    GetAccountTTLRequest,
    GetAuthorizationsRequest,
    GetAutoDownloadSettingsRequest,
    GetContentSettingsRequest,
    GetGlobalPrivacySettingsRequest,
    GetNotifySettingsRequest,
    GetPrivacyRequest,
)
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.contacts import GetContactsRequest, GetStatusesRequest
from telethon.tl.functions.help import GetAppConfigRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import (
    InputNotifyBroadcasts,
    InputNotifyChats,
    InputNotifyUsers,
    InputPrivacyKeyAbout,
    InputPrivacyKeyForwards,
    InputPrivacyKeyPhoneNumber,
    InputPrivacyKeyProfilePhoto,
    InputPrivacyKeyStatusTimestamp,
    InputUserSelf,
    UserStatusOnline,
)

from core.telegram_client._action_results import _DispatchResult
from core.telegram_client._warm_browse import resolve_inline_bot

if TYPE_CHECKING:
    from collections.abc import Callable

    from telethon import TelegramClient
    from telethon.tl.tlobject import TLObject, TLRequest

    from schemas.telegram_actions import WarmCheckSettings, WarmViewProfile

# The three global notification scopes a client shows under Notifications and Sounds.
_NOTIFY_SCOPES: tuple[type[TLObject], ...] = (
    InputNotifyBroadcasts,
    InputNotifyUsers,
    InputNotifyChats,
)

_PRIVACY_KEYS: tuple[type[TLObject], ...] = (
    InputPrivacyKeyPhoneNumber,
    InputPrivacyKeyForwards,
    InputPrivacyKeyProfilePhoto,
    InputPrivacyKeyStatusTimestamp,
    InputPrivacyKeyAbout,
)


# Zero-risk settings reads; each entry builds a fresh request.
_SETTINGS_READS: tuple[Callable[[], TLRequest], ...] = (
    *(partial(GetPrivacyRequest, key=key()) for key in _PRIVACY_KEYS),
    GetAuthorizationsRequest,
    GetContentSettingsRequest,
    GetGlobalPrivacySettingsRequest,
    GetAccountTTLRequest,
    GetAutoDownloadSettingsRequest,
    partial(GetAppConfigRequest, hash=0),
)


async def read_contacts(client: TelegramClient) -> _DispatchResult:
    """Read-only contact sync — the list plus who is online right now."""
    contacts = await client(GetContactsRequest(hash=0))
    statuses = await client(GetStatusesRequest())
    online = sum(
        1 for status in statuses if isinstance(getattr(status, "status", None), UserStatusOnline)
    )
    # ``ContactsNotModified`` carries no list — it reads as zero contacts.
    return _DispatchResult(
        log_extra={"contacts": len(getattr(contacts, "contacts", ())), "online": online},
    )


async def read_notify_settings(client: TelegramClient) -> _DispatchResult:
    for scope in _NOTIFY_SCOPES:
        await client(GetNotifySettingsRequest(peer=scope()))  # ty: ignore[invalid-argument-type]
    return _DispatchResult(log_extra={"scopes": len(_NOTIFY_SCOPES)})


async def check_settings(client: TelegramClient, action: WarmCheckSettings) -> _DispatchResult:
    """Run ``action.calls`` settings reads, rotating through the table from ``action.offset``.

    Deterministic on purpose — core holds no randomness; the service draws both fields.
    The session count is the one useful side signal, reported when that read ran.
    """
    log_extra: dict[str, object] = {"calls": action.calls}
    for i in range(action.calls):
        request = _SETTINGS_READS[(action.offset + i) % len(_SETTINGS_READS)]()
        result = await client(request)
        if isinstance(request, GetAuthorizationsRequest):
            log_extra["sessions"] = len(getattr(result, "authorizations", ()))
    return _DispatchResult(log_extra=log_extra)


async def view_profile(client: TelegramClient, action: WarmViewProfile) -> _DispatchResult:
    """Open our own profile, a channel's info page or a whitelisted bot's; DM peers never."""
    if action.kind == "channel":
        if action.channel is None:
            msg = "warm_view_profile with kind='channel' needs a channel"
            raise ValueError(msg)
        entity = await client.get_input_entity(action.channel)
        await client(GetFullChannelRequest(channel=entity))  # ty: ignore[invalid-argument-type]
    elif action.kind == "bot":
        bot = await resolve_inline_bot(client, action.bot)
        await client(GetFullUserRequest(id=bot))  # ty: ignore[invalid-argument-type]
    else:
        await client(GetFullUserRequest(id=InputUserSelf()))
    return _DispatchResult()
