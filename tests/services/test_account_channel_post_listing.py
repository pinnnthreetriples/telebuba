"""Read and pagination contracts for account-owned channel posts."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.telegram_client import TelegramReadError
from schemas.telegram_actions import ListChannelPosts
from schemas.telegram_actions_channels import TelegramChannelPost, TelegramChannelPosts
from services.accounts import AccountActionError, list_account_channel_posts

if TYPE_CHECKING:
    from pydantic import BaseModel


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


def _patch_read(
    monkeypatch: pytest.MonkeyPatch,
    result: TelegramChannelPosts,
) -> list[tuple[str, object]]:
    calls: list[tuple[str, object]] = []

    async def execute_read(account_id: str, action: object) -> TelegramChannelPosts:
        calls.append((account_id, action))
        return result

    monkeypatch.setattr("services.accounts.channel_posts.execute_read", execute_read)
    return calls


@pytest.mark.asyncio
async def test_list_posts_full_page_builds_next_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_read(monkeypatch, _posts(3))

    page = await list_account_channel_posts("acc-1", 42, limit=3)

    assert [item.post_id for item in page.items] == [100, 99, 98]
    assert page.next_cursor == "98", "a full page points at its last post id"
    assert len(calls) == 1
    account_id, action = calls[0]
    assert account_id == "acc-1"
    assert isinstance(action, ListChannelPosts)
    assert action.channel_id == 42
    assert action.limit == 3
    assert action.offset_id == 0


@pytest.mark.asyncio
async def test_list_posts_short_page_still_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A short page is not the end because the gateway drops id-less entries."""
    calls = _patch_read(monkeypatch, _posts(2))

    page = await list_account_channel_posts("acc-1", 42, limit=3)

    assert page.next_cursor == "99"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_list_posts_empty_page_ends_pagination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_read(monkeypatch, _posts(0))

    page = await list_account_channel_posts("acc-1", 42, limit=3)

    assert page.items == []
    assert page.next_cursor is None
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_list_posts_cursor_becomes_offset_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_read(monkeypatch, _posts(0))

    await list_account_channel_posts("acc-1", 42, cursor="98")

    assert len(calls) == 1
    account_id, action = calls[0]
    assert account_id == "acc-1"
    assert isinstance(action, ListChannelPosts)
    assert action.channel_id == 42
    assert action.offset_id == 98
    assert action.limit == settings.channels.posts_page_limit


@pytest.mark.asyncio
@pytest.mark.parametrize("cursor", ["abc", "-5", "0", "8589934592"])
async def test_list_posts_malformed_cursor_raises_value_error(
    monkeypatch: pytest.MonkeyPatch,
    cursor: str,
) -> None:
    calls = _patch_read(monkeypatch, _posts(0))

    with pytest.raises(ValueError, match="cursor"):
        await list_account_channel_posts("acc-1", 42, cursor=cursor)
    assert calls == []


@pytest.mark.asyncio
async def test_list_posts_read_error_maps_to_stable_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_read(_account_id: str, _action: object) -> BaseModel:
        reason = "RPC: ChannelPrivateError"
        raise TelegramReadError(reason)

    monkeypatch.setattr("services.accounts.channel_posts.execute_read", failing_read)

    with pytest.raises(AccountActionError) as excinfo:
        await list_account_channel_posts("acc-1", 42)
    assert excinfo.value.code == "channel_read_failed"
    assert excinfo.value.retry_after_seconds is None


@pytest.mark.asyncio
async def test_list_posts_flood_wait_keeps_the_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def flooded_read(_account_id: str, _action: object) -> BaseModel:
        reason = "FloodWait(30s)"
        raise TelegramReadError(reason, kind="flood_wait", seconds=30)

    monkeypatch.setattr("services.accounts.channel_posts.execute_read", flooded_read)

    with pytest.raises(AccountActionError) as excinfo:
        await list_account_channel_posts("acc-1", 42)
    assert excinfo.value.code == "flood_wait"
    assert excinfo.value.retry_after_seconds == 30
