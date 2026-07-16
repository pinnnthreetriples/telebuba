"""Own-channel management flows — create / list / detail / update / photo / delete.

``execute`` / ``execute_read`` are imported at module scope so tests can
monkeypatch ``services.accounts.channels.execute`` / ``.execute_read``.

No profile-cache involvement: channel data is not part of the profile
snapshot, so there is nothing to invalidate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from pydantic import ValidationError

from core.config import settings
from core.logging import log_event
from core.telegram_client import TelegramReadError, execute, execute_read
from schemas.api import Page
from schemas.channels import ChannelDetailView, ChannelUsernameCheckView, ChannelView
from schemas.telegram_actions import (
    CheckChannelUsername,
    CreateChannel,
    DeleteChannel,
    EditChannel,
    GetOwnChannel,
    ListOwnChannels,
    SetChannelPhoto,
)
from services.accounts._result import AccountActionError, raise_for_result
from services.accounts._uploads import _PROFILE_PHOTO_SUFFIXES, _validate_upload

if TYPE_CHECKING:
    from pydantic import BaseModel

    from schemas.channels import ChannelCreateRequest, ChannelUpdateRequest
    from schemas.telegram_actions import ActionResult, TelegramReadAction
    from schemas.telegram_actions_channels import (
        ChannelUsernameCheck,
        TelegramOwnChannelDetail,
        TelegramOwnChannels,
    )

__all__ = [
    "check_account_channel_username",
    "create_account_channel",
    "delete_account_channel",
    "get_account_channel",
    "list_account_channels",
    "set_account_channel_photo",
    "update_account_channel",
]


async def _read(account_id: str, action: TelegramReadAction) -> BaseModel:
    """Read via the gateway; wrap gateway failures in the stable read code.

    ``TelegramAccountNotFoundError`` propagates untouched (same contract as
    the profile-media services).
    """
    try:
        return await execute_read(account_id, action)
    except TelegramReadError as exc:
        code = "channel_read_failed"
        raise AccountActionError(code) from exc


async def create_account_channel(
    account_id: str,
    data: ChannelCreateRequest,
) -> ActionResult:
    result = await execute(
        account_id,
        CreateChannel(title=data.title, about=data.about, username=data.username),
    )
    raise_for_result(result)
    await log_event(
        "INFO",
        "account_channel_created",
        account_id=account_id,
        extra={
            "title": data.title,
            "has_username": data.username is not None,
            "channel_id": result.channel_id,
        },
    )
    return result


async def list_account_channels(account_id: str) -> Page[ChannelView]:
    """All owned channels in one page — the fleet never owns enough to paginate."""
    channels = cast(
        "TelegramOwnChannels",
        await _read(account_id, ListOwnChannels(limit=settings.channels.list_limit)),
    )
    items = [
        ChannelView(
            channel_id=str(item.channel_id),
            title=item.title,
            username=item.username,
            participants_count=item.participants_count,
        )
        for item in channels.items
    ]
    return Page(items=items, next_cursor=None)


async def get_account_channel(account_id: str, channel_id: int) -> ChannelDetailView:
    detail = cast(
        "TelegramOwnChannelDetail",
        await _read(account_id, GetOwnChannel(channel_id=channel_id)),
    )
    return ChannelDetailView(
        channel_id=str(detail.channel_id),
        title=detail.title,
        username=detail.username,
        about=detail.about,
        participants_count=detail.participants_count,
    )


async def update_account_channel(
    account_id: str,
    channel_id: int,
    data: ChannelUpdateRequest,
) -> ActionResult:
    result = await execute(
        account_id,
        EditChannel(channel_id=channel_id, title=data.title, about=data.about),
    )
    raise_for_result(result)
    await log_event(
        "INFO",
        "account_channel_updated",
        account_id=account_id,
        extra={
            "channel_id": channel_id,
            "has_title": data.title is not None,
            "has_about": data.about is not None,
        },
    )
    return result


async def set_account_channel_photo(
    account_id: str,
    channel_id: int,
    *,
    filename: str,
    content: bytes,
) -> ActionResult:
    _validate_upload(
        filename=filename,
        content=content,
        max_bytes=settings.channels.avatar_max_bytes,
        allowed_suffixes=_PROFILE_PHOTO_SUFFIXES,
        label="channel photo",
    )
    result = await execute(
        account_id,
        SetChannelPhoto(channel_id=channel_id, filename=filename, content=content),
    )
    raise_for_result(result)
    await log_event(
        "INFO",
        "account_channel_photo_updated",
        account_id=account_id,
        extra={"channel_id": channel_id, "filename": filename},
    )
    return result


async def delete_account_channel(account_id: str, channel_id: int) -> ActionResult:
    result = await execute(account_id, DeleteChannel(channel_id=channel_id))
    raise_for_result(result)
    await log_event(
        "INFO",
        "account_channel_deleted",
        account_id=account_id,
        extra={"channel_id": channel_id},
    )
    return result


async def check_account_channel_username(
    account_id: str,
    username: str,
) -> ChannelUsernameCheckView:
    """Handle availability probe — total over all inputs.

    A handle that fails our pattern never reaches Telegram: it maps straight
    to the stable invalid code, so the as-you-type UI check gets a structured
    verdict instead of a validation 400.
    """
    try:
        action = CheckChannelUsername(username=username)
    except ValidationError:
        return ChannelUsernameCheckView(available=False, code="channel_username_invalid")
    check = cast("ChannelUsernameCheck", await _read(account_id, action))
    return ChannelUsernameCheckView(available=check.available, code=check.code)
