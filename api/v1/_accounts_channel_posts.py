"""Own-channel post endpoints — publish / list / edit / delete.

Split-sibling of ``accounts.py`` (same pattern as ``_accounts_media.py``);
mounted onto the accounts router. Shares the channel-id decoding and
pre-read size guard with ``_accounts_channels``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi import status as http_status

from api.v1._accounts_channels import _decode_channel_id, _reject_oversize
from core.config import settings
from schemas.api import Page
from schemas.channels import ChannelPostEditRequest, ChannelPostView
from schemas.telegram_actions import ActionResult
from services import accounts

# No tags: mounted onto the accounts router (already tagged "accounts").
channel_posts_router = APIRouter()


@channel_posts_router.post(
    "/accounts/{account_id}/channels/{channel_id}/posts",
    response_model=ActionResult,
    operation_id="publishAccountChannelPost",
)
async def publish_account_channel_post(
    account_id: str,
    channel_id: str,
    text: Annotated[str, Form()] = "",
    file: Annotated[UploadFile | None, File()] = None,
) -> ActionResult:
    content: bytes | None = None
    filename: str | None = None
    if file is not None:
        # Pre-read DoS guard at the LARGER (video) cap; the service enforces
        # the exact per-kind cap after the suffix decides photo vs video.
        _reject_oversize(file, settings.channels.post_video_max_bytes)
        content = await file.read()
        filename = file.filename or "media"
    try:
        return await accounts.publish_account_channel_post(
            account_id,
            _decode_channel_id(channel_id),
            text=text,
            filename=filename,
            content=content,
        )
    except accounts.AccountActionError:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@channel_posts_router.get(
    "/accounts/{account_id}/channels/{channel_id}/posts",
    response_model=Page[ChannelPostView],
    operation_id="listAccountChannelPosts",
)
async def list_account_channel_posts(
    account_id: str,
    channel_id: str,
    cursor: str | None = None,
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
) -> Page[ChannelPostView]:
    try:
        return await accounts.list_account_channel_posts(
            account_id,
            _decode_channel_id(channel_id),
            cursor=cursor,
            limit=limit,
        )
    except accounts.AccountActionError:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@channel_posts_router.post(
    "/accounts/{account_id}/channels/{channel_id}/posts/{post_id}/edit",
    response_model=ActionResult,
    operation_id="editAccountChannelPost",
)
async def edit_account_channel_post(
    account_id: str,
    channel_id: str,
    post_id: int,
    body: ChannelPostEditRequest,
) -> ActionResult:
    try:
        return await accounts.edit_account_channel_post(
            account_id,
            _decode_channel_id(channel_id),
            post_id,
            text=body.text,
        )
    except accounts.AccountActionError:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@channel_posts_router.post(
    "/accounts/{account_id}/channels/{channel_id}/posts/{post_id}/delete",
    response_model=ActionResult,
    operation_id="deleteAccountChannelPost",
)
async def delete_account_channel_post(
    account_id: str,
    channel_id: str,
    post_id: int,
) -> ActionResult:
    try:
        return await accounts.delete_account_channel_post(
            account_id,
            _decode_channel_id(channel_id),
            post_id,
        )
    except accounts.AccountActionError:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
