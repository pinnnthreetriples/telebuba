"""Own-channel management endpoints — create / list / detail / update / photo / delete.

Split-sibling of ``accounts.py`` (same pattern as ``_accounts_media.py``);
mounted onto the accounts router via ``include_router``. Channel ids travel as
int64 decimal strings (see :mod:`schemas.channels`), so the path params arrive
as strings and are decoded here.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi import status as http_status

from api.v1._errors import service_errors_to_http
from core.config import settings
from schemas.api import Page
from schemas.channels import (
    ChannelCreateRequest,
    ChannelDetailView,
    ChannelUpdateRequest,
    ChannelUsernameCheckView,
    ChannelView,
)
from schemas.telegram_actions import ActionResult
from services import accounts

# No tags: mounted onto the accounts router (already tagged "accounts").
channels_router = APIRouter()


def _decode_channel_id(value: str) -> int:
    """Parse an int64 channel id carried as a string in the path, or 400.

    Channel ids cross the JSON boundary as decimal strings so the SPA never
    rounds them past 2^53 (see schemas.channels._Int64Str).
    """
    try:
        channel_id = int(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="invalid channel id",
        ) from exc
    if channel_id <= 0:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="invalid channel id",
        )
    return channel_id


def _reject_oversize(file: UploadFile, max_bytes: int) -> None:
    """Refuse an upload by its declared size BEFORE buffering it into RAM.

    ``UploadFile.size`` comes from the multipart headers (may be absent);
    the service re-checks the actual byte count after the read.
    """
    if file.size is not None and file.size > max_bytes:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="file too large",
        )


@channels_router.post(
    "/accounts/{account_id}/channels",
    response_model=ActionResult,
    operation_id="createAccountChannel",
)
async def create_account_channel(account_id: str, body: ChannelCreateRequest) -> ActionResult:
    with service_errors_to_http():
        return await accounts.create_account_channel(account_id, body)


@channels_router.get(
    "/accounts/{account_id}/channels",
    response_model=Page[ChannelView],
    operation_id="listAccountChannels",
)
async def list_account_channels(account_id: str) -> Page[ChannelView]:
    return await accounts.list_account_channels(account_id)


# Flat path on purpose: nesting it as /channels/username-check would collide
# with the /channels/{channel_id} route (route-order trap).
@channels_router.get(
    "/accounts/{account_id}/channel-username-check",
    response_model=ChannelUsernameCheckView,
    operation_id="checkAccountChannelUsername",
)
async def check_account_channel_username(
    account_id: str,
    username: Annotated[str, Query(min_length=1)],
) -> ChannelUsernameCheckView:
    return await accounts.check_account_channel_username(account_id, username)


@channels_router.get(
    "/accounts/{account_id}/channels/{channel_id}",
    response_model=ChannelDetailView,
    operation_id="getAccountChannel",
)
async def get_account_channel(account_id: str, channel_id: str) -> ChannelDetailView:
    return await accounts.get_account_channel(account_id, _decode_channel_id(channel_id))


@channels_router.post(
    "/accounts/{account_id}/channels/{channel_id}/update",
    response_model=ActionResult,
    operation_id="updateAccountChannel",
)
async def update_account_channel(
    account_id: str,
    channel_id: str,
    body: ChannelUpdateRequest,
) -> ActionResult:
    with service_errors_to_http():
        return await accounts.update_account_channel(
            account_id,
            _decode_channel_id(channel_id),
            body,
        )


@channels_router.post(
    "/accounts/{account_id}/channels/{channel_id}/photo",
    response_model=ActionResult,
    operation_id="setAccountChannelPhoto",
)
async def set_account_channel_photo(
    account_id: str,
    channel_id: str,
    file: Annotated[UploadFile, File()],
) -> ActionResult:
    _reject_oversize(file, settings.channels.avatar_max_bytes)
    content = await file.read()
    with service_errors_to_http():
        return await accounts.set_account_channel_photo(
            account_id,
            _decode_channel_id(channel_id),
            filename=file.filename or "photo.jpg",
            content=content,
        )


@channels_router.post(
    "/accounts/{account_id}/channels/{channel_id}/delete",
    response_model=ActionResult,
    operation_id="deleteAccountChannel",
)
async def delete_account_channel(account_id: str, channel_id: str) -> ActionResult:
    with service_errors_to_http():
        return await accounts.delete_account_channel(account_id, _decode_channel_id(channel_id))
