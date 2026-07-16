"""Own-channel post flows — publish / list / edit / delete.

``execute`` / ``execute_read`` are imported at module scope so tests can
monkeypatch ``services.accounts.channel_posts.execute`` / ``.execute_read``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from core.config import settings
from core.logging import log_event
from core.telegram_client import TelegramReadError, execute, execute_read
from schemas.api import Page
from schemas.channels import ChannelPostView
from schemas.telegram_actions import (
    DeleteChannelPost,
    EditChannelPost,
    ListChannelPosts,
    PublishChannelPost,
)
from schemas.telegram_actions_channels import CHANNEL_POST_ID_MAX
from services.accounts._result import AccountActionError, raise_for_result
from services.accounts._uploads import (
    _PROFILE_PHOTO_SUFFIXES,
    _STORY_VIDEO_SUFFIXES,
    _validate_upload,
)

if TYPE_CHECKING:
    from pydantic import BaseModel

    from schemas.telegram_actions import ActionResult, TelegramReadAction
    from schemas.telegram_actions_channels import TelegramChannelPosts

__all__ = [
    "delete_account_channel_post",
    "edit_account_channel_post",
    "list_account_channel_posts",
    "publish_account_channel_post",
]


async def _read(account_id: str, action: TelegramReadAction) -> BaseModel:
    """Read via the gateway; wrap gateway failures in the stable read code."""
    try:
        return await execute_read(account_id, action)
    except TelegramReadError as exc:
        code = "channel_read_failed"
        raise AccountActionError(code) from exc


def _derive_media_kind(filename: str) -> Literal["photo", "video"]:
    """Post media kind from the upload's suffix — photo set vs video set."""
    suffix = Path(filename).suffix.lower()
    if suffix in _PROFILE_PHOTO_SUFFIXES:
        return "photo"
    if suffix in _STORY_VIDEO_SUFFIXES:
        return "video"
    allowed = ", ".join(sorted(_PROFILE_PHOTO_SUFFIXES | _STORY_VIDEO_SUFFIXES))
    msg = f"post media must be one of: {allowed}"
    raise ValueError(msg)


async def publish_account_channel_post(
    account_id: str,
    channel_id: int,
    *,
    text: str = "",
    filename: str | None = None,
    content: bytes | None = None,
) -> ActionResult:
    media_kind: Literal["photo", "video"] | None = None
    if filename is not None and content is not None:
        media_kind = _derive_media_kind(filename)
        _validate_upload(
            filename=filename,
            content=content,
            max_bytes=(
                settings.channels.post_photo_max_bytes
                if media_kind == "photo"
                else settings.channels.post_video_max_bytes
            ),
            allowed_suffixes=(
                _PROFILE_PHOTO_SUFFIXES if media_kind == "photo" else _STORY_VIDEO_SUFFIXES
            ),
            label=f"post {media_kind}",
        )
    result = await execute(
        account_id,
        PublishChannelPost(
            channel_id=channel_id,
            text=text,
            filename=filename,
            content=content,
            media_kind=media_kind,
        ),
    )
    raise_for_result(result)
    await log_event(
        "INFO",
        "account_channel_post_published",
        account_id=account_id,
        extra={
            "channel_id": channel_id,
            "media_kind": media_kind,
            "message_id": result.message_id,
        },
    )
    return result


def _decode_cursor(cursor: str | None) -> int:
    """Cursor = the previous page's last post id (paging strictly below it).

    Bounded to the int32 message-id window - a numeric-but-oversized cursor is
    just as malformed as a non-numeric one (it can't name a real post and
    would blow up in TL struct packing instead of a 400).
    """
    if cursor is None:
        return 0
    try:
        offset_id = int(cursor)
    except ValueError as exc:
        msg = "invalid pagination cursor"
        raise ValueError(msg) from exc
    if offset_id <= 0 or offset_id > CHANNEL_POST_ID_MAX:
        msg = "invalid pagination cursor"
        raise ValueError(msg)
    return offset_id


async def list_account_channel_posts(
    account_id: str,
    channel_id: int,
    *,
    cursor: str | None = None,
    limit: int | None = None,
) -> Page[ChannelPostView]:
    page_limit = limit if limit is not None else settings.channels.posts_page_limit
    offset_id = _decode_cursor(cursor)
    posts = cast(
        "TelegramChannelPosts",
        await _read(
            account_id,
            ListChannelPosts(channel_id=channel_id, limit=page_limit, offset_id=offset_id),
        ),
    )
    items = [
        ChannelPostView(
            post_id=post.post_id,
            date_unix=post.date_unix,
            text=post.text,
            media_kind=post.media_kind,
            views=post.views,
        )
        for post in posts.items
    ]
    # A full page means there may be older posts; a short page is the end.
    next_cursor = str(items[-1].post_id) if len(items) == page_limit else None
    return Page(items=items, next_cursor=next_cursor)


async def edit_account_channel_post(
    account_id: str,
    channel_id: int,
    post_id: int,
    *,
    text: str,
) -> ActionResult:
    result = await execute(
        account_id,
        EditChannelPost(channel_id=channel_id, post_id=post_id, text=text),
    )
    raise_for_result(result)
    await log_event(
        "INFO",
        "account_channel_post_edited",
        account_id=account_id,
        extra={"channel_id": channel_id, "post_id": post_id},
    )
    return result


async def delete_account_channel_post(
    account_id: str,
    channel_id: int,
    post_id: int,
) -> ActionResult:
    result = await execute(
        account_id,
        DeleteChannelPost(channel_id=channel_id, post_id=post_id),
    )
    raise_for_result(result)
    await log_event(
        "INFO",
        "account_channel_post_deleted",
        account_id=account_id,
        extra={"channel_id": channel_id, "post_id": post_id},
    )
    return result
