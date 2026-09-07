"""Warming reads of the account's own state: contacts, notifications, settings, profiles.

Every request here is a read a mobile client issues when its owner opens a settings
screen. No contact is ever imported — the operator's decision. The one write is the
Premium emoji status, and it expires by itself.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING

from telethon import errors
from telethon.tl.functions.account import (
    GetAccountTTLRequest,
    GetAuthorizationsRequest,
    GetAutoDownloadSettingsRequest,
    GetContentSettingsRequest,
    GetDefaultEmojiStatusesRequest,
    GetGlobalPrivacySettingsRequest,
    GetNotifySettingsRequest,
    GetPrivacyRequest,
    UpdateEmojiStatusRequest,
)
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.contacts import GetContactsRequest, GetStatusesRequest
from telethon.tl.functions.help import GetAppConfigRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import (
    EmojiStatus,
    EmojiStatusEmpty,
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

    from schemas.telegram_actions import WarmCheckSettings, WarmEmojiStatus, WarmViewProfile

# Not Premium, or the default set moved on under us — nothing to set this cycle.
_STATUS_SKIPS: dict[type[Exception], str] = {
    errors.PremiumAccountRequiredError: "premium_required",
    errors.DocumentInvalidError: "status_unavailable",
}

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


async def emoji_status(client: TelegramClient, action: WarmEmojiStatus) -> _DispatchResult:
    """Set one of Telegram's default emoji statuses for ``until_hours``, or clear it.

    Deterministic on purpose — ``status_index`` wraps modulo the list; the service draws
    it. ``until`` makes the write reversible without a second one.
    """
    log_extra: dict[str, object] = {"clear": action.clear, "until_hours": action.until_hours}
    status: EmojiStatus | EmojiStatusEmpty = EmojiStatusEmpty()
    try:
        if not action.clear:
            # ``EmojiStatusesNotModified`` (hash matched) carries no list — nothing to pick.
            defaults = await client(GetDefaultEmojiStatusesRequest(hash=0))
            ids = [
                s.document_id
                for s in getattr(defaults, "statuses", ())
                if isinstance(s, EmojiStatus)
            ]
            if not ids:
                return _DispatchResult(log_extra={"warm_skip": "no_statuses"})
            # Whole minutes: the client's picker only offers those, so seconds would fingerprint.
            until = (datetime.now(UTC) + timedelta(hours=action.until_hours)).replace(
                second=0, microsecond=0
            )
            status = EmojiStatus(document_id=ids[action.status_index % len(ids)], until=until)
        await client(UpdateEmojiStatusRequest(emoji_status=status))
    except tuple(_STATUS_SKIPS) as exc:
        return _DispatchResult(log_extra={"warm_skip": _STATUS_SKIPS[type(exc)]})
    except errors.EmojiNotModifiedError:
        pass  # already wearing it — the state we wanted
    return _DispatchResult(log_extra=log_extra)
