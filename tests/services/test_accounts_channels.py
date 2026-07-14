"""Tests for the channel + channel-post services.

External collaborators are monkeypatched on their owning submodules
(``services.accounts.channels`` / ``services.accounts.channel_posts``) — the
module-scope ``execute`` / ``execute_read`` imports are the patch seams.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from core.telegram_client import TelegramReadError
from schemas.channels import ChannelCreateRequest, ChannelUpdateRequest
from schemas.telegram_actions import (
    ActionResult,
    CheckChannelUsername,
    CreateChannel,
    DeleteChannel,
    DeleteChannelPost,
    EditChannel,
    EditChannelPost,
    GetOwnChannel,
    ListChannelPosts,
    ListOwnChannels,
    PublishChannelPost,
    SetChannelPhoto,
)
from schemas.telegram_actions_channels import (
    ChannelUsernameCheck,
    TelegramChannelPost,
    TelegramChannelPosts,
    TelegramOwnChannel,
    TelegramOwnChannelDetail,
    TelegramOwnChannels,
)
from services.accounts import (
    AccountActionError,
    check_account_channel_username,
    create_account_channel,
    delete_account_channel,
    delete_account_channel_post,
    edit_account_channel_post,
    get_account_channel,
    list_account_channel_posts,
    list_account_channels,
    publish_account_channel_post,
    set_account_channel_photo,
    update_account_channel,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from pydantic import BaseModel


@pytest.fixture(autouse=True)
def _isolate_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.telegram, "session_dir", tmp_path / "sessions")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()
    yield
    reset_logging_for_tests()


def _ok(action_type: str, account_id: str, **kwargs: object) -> ActionResult:
    return ActionResult(status="ok", action_type=action_type, account_id=account_id, **kwargs)  # ty: ignore[invalid-argument-type]


def _patch_execute(
    monkeypatch: pytest.MonkeyPatch,
    module: str,
    result: ActionResult,
) -> list[object]:
    captured: list[object] = []

    async def fake_execute(_account_id: str, action: object) -> ActionResult:
        captured.append(action)
        return result

    monkeypatch.setattr(f"services.accounts.{module}.execute", fake_execute)
    return captured


def _patch_read(
    monkeypatch: pytest.MonkeyPatch,
    module: str,
    result: BaseModel,
) -> list[object]:
    captured: list[object] = []

    async def fake_execute_read(_account_id: str, action: object) -> BaseModel:
        captured.append(action)
        return result

    monkeypatch.setattr(f"services.accounts.{module}.execute_read", fake_execute_read)
    return captured


def _patch_log(monkeypatch: pytest.MonkeyPatch, module: str) -> list[tuple[str, str]]:
    events: list[tuple[str, str]] = []

    async def fake_log(
        level: str,
        event: str,
        account_id: str | None = None,  # noqa: ARG001
        extra: dict[str, object] | None = None,  # noqa: ARG001
    ) -> None:
        events.append((level, event))

    monkeypatch.setattr(f"services.accounts.{module}.log_event", fake_log)
    return events


# --------------------------------------------------------------------------- #
# Channels
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_create_account_channel_executes_and_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_execute(
        monkeypatch,
        "channels",
        _ok("channel_create", "acc-1", channel_id="42"),
    )
    events = _patch_log(monkeypatch, "channels")

    result = await create_account_channel(
        "acc-1",
        ChannelCreateRequest(title="Mine", about="Desc", username="my_channel"),
    )

    assert result.channel_id == "42"
    action = captured[0]
    assert isinstance(action, CreateChannel)
    assert action.title == "Mine"
    assert action.about == "Desc"
    assert action.username == "my_channel"
    assert events == [("INFO", "account_channel_created")]


@pytest.mark.asyncio
async def test_create_account_channel_failed_result_raises_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_execute(
        monkeypatch,
        "channels",
        ActionResult(
            status="failed",
            action_type="channel_create",
            account_id="acc-1",
            error_message="channel_username_occupied",
        ),
    )
    events = _patch_log(monkeypatch, "channels")

    with pytest.raises(AccountActionError) as excinfo:
        await create_account_channel("acc-1", ChannelCreateRequest(title="Mine"))
    assert excinfo.value.code == "channel_username_occupied"
    assert events == [], "a failed action must not log a success event"


@pytest.mark.asyncio
async def test_list_account_channels_maps_ids_to_strings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_read(
        monkeypatch,
        "channels",
        TelegramOwnChannels(
            items=[
                TelegramOwnChannel(
                    channel_id=9_007_199_254_740_993,  # 2^53 + 1 — JS-unsafe
                    title="Big",
                    username=None,
                    participants_count=3,
                ),
            ],
        ),
    )

    page = await list_account_channels("acc-1")

    assert page.next_cursor is None
    assert len(page.items) == 1
    assert page.items[0].channel_id == "9007199254740993"
    assert page.items[0].participants_count == 3
    action = captured[0]
    assert isinstance(action, ListOwnChannels)
    assert action.limit == settings.channels.list_limit


@pytest.mark.asyncio
async def test_list_account_channels_read_error_maps_to_stable_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_read(_account_id: str, _action: object) -> BaseModel:
        reason = "RPC: ChannelPrivateError"
        raise TelegramReadError(reason)

    monkeypatch.setattr("services.accounts.channels.execute_read", failing_read)

    with pytest.raises(AccountActionError) as excinfo:
        await list_account_channels("acc-1")
    assert excinfo.value.code == "channel_read_failed"


@pytest.mark.asyncio
async def test_get_account_channel_maps_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_read(
        monkeypatch,
        "channels",
        TelegramOwnChannelDetail(
            channel_id=42,
            title="Mine",
            username="mine",
            about="All about it",
            participants_count=7,
        ),
    )

    view = await get_account_channel("acc-1", 42)

    assert view.channel_id == "42"
    assert view.about == "All about it"
    assert view.participants_count == 7
    action = captured[0]
    assert isinstance(action, GetOwnChannel)
    assert action.channel_id == 42


@pytest.mark.asyncio
async def test_update_account_channel_threads_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_execute(monkeypatch, "channels", _ok("channel_edit", "acc-1"))
    events = _patch_log(monkeypatch, "channels")

    await update_account_channel(
        "acc-1",
        42,
        ChannelUpdateRequest(title="New", about=""),
    )

    action = captured[0]
    assert isinstance(action, EditChannel)
    assert action.channel_id == 42
    assert action.title == "New"
    assert action.about == ""
    assert events == [("INFO", "account_channel_updated")]


@pytest.mark.asyncio
async def test_update_account_channel_rejects_empty_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No field set → the EditChannel action model refuses (ValueError family)."""
    _patch_execute(monkeypatch, "channels", _ok("channel_edit", "acc-1"))

    with pytest.raises(ValueError, match="title/about"):
        await update_account_channel("acc-1", 42, ChannelUpdateRequest())


@pytest.mark.asyncio
async def test_set_account_channel_photo_validates_and_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_execute(monkeypatch, "channels", _ok("channel_set_photo", "acc-1"))
    events = _patch_log(monkeypatch, "channels")

    await set_account_channel_photo("acc-1", 42, filename="logo.png", content=b"png")

    action = captured[0]
    assert isinstance(action, SetChannelPhoto)
    assert action.filename == "logo.png"
    assert events == [("INFO", "account_channel_photo_updated")]


@pytest.mark.asyncio
async def test_set_account_channel_photo_rejects_wrong_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_execute(monkeypatch, "channels", _ok("channel_set_photo", "acc-1"))

    with pytest.raises(ValueError, match="channel photo"):
        await set_account_channel_photo("acc-1", 42, filename="logo.gif", content=b"gif")
    assert captured == []


@pytest.mark.asyncio
async def test_set_account_channel_photo_rejects_oversize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_execute(monkeypatch, "channels", _ok("channel_set_photo", "acc-1"))
    monkeypatch.setattr(settings.channels, "avatar_max_bytes", 4)

    with pytest.raises(ValueError, match="too large"):
        await set_account_channel_photo("acc-1", 42, filename="logo.png", content=b"12345")


@pytest.mark.asyncio
async def test_delete_account_channel_executes_and_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_execute(monkeypatch, "channels", _ok("channel_delete", "acc-1"))
    events = _patch_log(monkeypatch, "channels")

    await delete_account_channel("acc-1", 42)

    action = captured[0]
    assert isinstance(action, DeleteChannel)
    assert action.channel_id == 42
    assert events == [("INFO", "account_channel_deleted")]


@pytest.mark.asyncio
async def test_check_channel_username_pattern_invalid_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A handle failing the pattern never reaches Telegram."""
    captured = _patch_read(
        monkeypatch,
        "channels",
        ChannelUsernameCheck(available=True),
    )

    view = await check_account_channel_username("acc-1", "1bad")

    assert view.available is False
    assert view.code == "channel_username_invalid"
    assert captured == []


@pytest.mark.asyncio
async def test_check_channel_username_maps_gateway_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_read(
        monkeypatch,
        "channels",
        ChannelUsernameCheck(available=False, code="channel_username_occupied"),
    )

    view = await check_account_channel_username("acc-1", "good_handle")

    assert view.available is False
    assert view.code == "channel_username_occupied"
    action = captured[0]
    assert isinstance(action, CheckChannelUsername)
    assert action.username == "good_handle"


# --------------------------------------------------------------------------- #
# Channel posts
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_publish_text_post_executes_and_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_execute(
        monkeypatch,
        "channel_posts",
        _ok("channel_post_publish", "acc-1", message_id=5),
    )
    events = _patch_log(monkeypatch, "channel_posts")

    result = await publish_account_channel_post("acc-1", 42, text="hello")

    assert result.message_id == 5
    action = captured[0]
    assert isinstance(action, PublishChannelPost)
    assert action.text == "hello"
    assert action.media_kind is None
    assert events == [("INFO", "account_channel_post_published")]


@pytest.mark.asyncio
async def test_publish_post_without_text_or_media_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_execute(monkeypatch, "channel_posts", _ok("channel_post_publish", "acc-1"))

    with pytest.raises(ValueError, match="text"):
        await publish_account_channel_post("acc-1", 42, text="")
    assert captured == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "expected_kind"),
    [
        ("pic.JPG", "photo"),
        ("pic.webp", "photo"),
        ("clip.mp4", "video"),
        ("clip.MOV", "video"),
    ],
)
async def test_publish_post_derives_media_kind_from_suffix(
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    expected_kind: str,
) -> None:
    captured = _patch_execute(monkeypatch, "channel_posts", _ok("channel_post_publish", "acc-1"))
    _patch_log(monkeypatch, "channel_posts")

    await publish_account_channel_post(
        "acc-1",
        42,
        text="caption",
        filename=filename,
        content=b"bytes",
    )

    action = captured[0]
    assert isinstance(action, PublishChannelPost)
    assert action.media_kind == expected_kind
    assert action.filename == filename


@pytest.mark.asyncio
async def test_publish_post_unknown_suffix_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_execute(monkeypatch, "channel_posts", _ok("channel_post_publish", "acc-1"))

    with pytest.raises(ValueError, match="post media"):
        await publish_account_channel_post(
            "acc-1",
            42,
            text="",
            filename="doc.pdf",
            content=b"pdf",
        )
    assert captured == []


@pytest.mark.asyncio
async def test_publish_post_partial_media_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A filename without content never reaches the executor (all-or-none)."""
    captured = _patch_execute(monkeypatch, "channel_posts", _ok("channel_post_publish", "acc-1"))

    with pytest.raises(ValueError, match="together"):
        await publish_account_channel_post("acc-1", 42, text="hi", filename="pic.jpg")
    assert captured == []


@pytest.mark.asyncio
async def test_publish_post_media_caption_over_1024_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_execute(monkeypatch, "channel_posts", _ok("channel_post_publish", "acc-1"))

    with pytest.raises(ValueError, match="1024"):
        await publish_account_channel_post(
            "acc-1",
            42,
            text="x" * 1025,
            filename="pic.jpg",
            content=b"jpg",
        )
    assert captured == []


@pytest.mark.asyncio
async def test_publish_post_video_size_cap_applies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_execute(monkeypatch, "channel_posts", _ok("channel_post_publish", "acc-1"))
    monkeypatch.setattr(settings.channels, "post_video_max_bytes", 4)

    with pytest.raises(ValueError, match="too large"):
        await publish_account_channel_post(
            "acc-1",
            42,
            text="",
            filename="clip.mp4",
            content=b"12345",
        )


@pytest.mark.asyncio
async def test_publish_post_failed_result_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_execute(
        monkeypatch,
        "channel_posts",
        ActionResult(
            status="failed",
            action_type="channel_post_publish",
            account_id="acc-1",
            error_message="chat_admin_required",
        ),
    )

    with pytest.raises(AccountActionError) as excinfo:
        await publish_account_channel_post("acc-1", 42, text="hi")
    assert excinfo.value.code == "chat_admin_required"


def _posts(count: int, *, start: int = 100) -> TelegramChannelPosts:
    return TelegramChannelPosts(
        items=[
            TelegramChannelPost(
                post_id=start - index,
                date_unix=1_750_000_000,
                text=f"post {start - index}",
                media_kind="none",
                views=None,
            )
            for index in range(count)
        ],
    )


@pytest.mark.asyncio
async def test_list_posts_full_page_builds_next_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_read(monkeypatch, "channel_posts", _posts(3))

    page = await list_account_channel_posts("acc-1", 42, limit=3)

    assert [item.post_id for item in page.items] == [100, 99, 98]
    assert page.next_cursor == "98", "a full page points at its last post id"
    action = captured[0]
    assert isinstance(action, ListChannelPosts)
    assert action.limit == 3
    assert action.offset_id == 0


@pytest.mark.asyncio
async def test_list_posts_short_page_ends_pagination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_read(monkeypatch, "channel_posts", _posts(2))

    page = await list_account_channel_posts("acc-1", 42, limit=3)

    assert page.next_cursor is None


@pytest.mark.asyncio
async def test_list_posts_cursor_becomes_offset_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_read(monkeypatch, "channel_posts", _posts(0))

    await list_account_channel_posts("acc-1", 42, cursor="98")

    action = captured[0]
    assert isinstance(action, ListChannelPosts)
    assert action.offset_id == 98
    assert action.limit == settings.channels.posts_page_limit


@pytest.mark.asyncio
@pytest.mark.parametrize("cursor", ["abc", "-5", "0"])
async def test_list_posts_malformed_cursor_raises_value_error(
    monkeypatch: pytest.MonkeyPatch,
    cursor: str,
) -> None:
    captured = _patch_read(monkeypatch, "channel_posts", _posts(0))

    with pytest.raises(ValueError, match="cursor"):
        await list_account_channel_posts("acc-1", 42, cursor=cursor)
    assert captured == []


@pytest.mark.asyncio
async def test_list_posts_read_error_maps_to_stable_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_read(_account_id: str, _action: object) -> BaseModel:
        reason = "FloodWait(30s)"
        raise TelegramReadError(reason)

    monkeypatch.setattr("services.accounts.channel_posts.execute_read", failing_read)

    with pytest.raises(AccountActionError) as excinfo:
        await list_account_channel_posts("acc-1", 42)
    assert excinfo.value.code == "channel_read_failed"


@pytest.mark.asyncio
async def test_edit_post_executes_and_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_execute(monkeypatch, "channel_posts", _ok("channel_post_edit", "acc-1"))
    events = _patch_log(monkeypatch, "channel_posts")

    await edit_account_channel_post("acc-1", 42, 10, text="fixed")

    action = captured[0]
    assert isinstance(action, EditChannelPost)
    assert action.post_id == 10
    assert action.text == "fixed"
    assert events == [("INFO", "account_channel_post_edited")]


@pytest.mark.asyncio
async def test_delete_post_executes_and_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_execute(monkeypatch, "channel_posts", _ok("channel_post_delete", "acc-1"))
    events = _patch_log(monkeypatch, "channel_posts")

    await delete_account_channel_post("acc-1", 42, 10)

    action = captured[0]
    assert isinstance(action, DeleteChannelPost)
    assert action.post_id == 10
    assert events == [("INFO", "account_channel_post_deleted")]
