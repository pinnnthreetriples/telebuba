"""Per-channel deletion accounting and backoff isolation contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.config import settings
from schemas.neurocomment import CommentRecord
from schemas.telegram_actions import CheckMessagesAliveResult
from services.neurocomment import _state, _sweep

pytestmark = pytest.mark.usefixtures("isolate_runtime")


def _comment(channel: str, message_id: int, *, account: str = "reader") -> CommentRecord:
    return CommentRecord(
        channel=channel,
        post_id=message_id,
        campaign_id="campaign",
        account_id=account,
        status="posted",
        comment_msg_id=message_id,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


@pytest.mark.asyncio
async def test_empty_fresh_delete_set_still_updates_window_without_delete_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_state, "channel_in_backoff", lambda *_a: False)
    monkeypatch.setattr(
        _sweep._seams,
        "execute_read",
        AsyncMock(return_value=CheckMessagesAliveResult(missing_ids=[2])),
    )
    monkeypatch.setattr(
        _sweep, "mark_comments_deleted", AsyncMock(return_value=SimpleNamespace(comments=[]))
    )
    register = Mock(return_value=None)
    log = AsyncMock()
    monkeypatch.setattr(_state, "register_channel_deletions", register)
    monkeypatch.setattr(_sweep, "log_event", log)

    now = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
    await _sweep._sweep_channel("@c", [_comment("@c", 1), _comment("@c", 2)], now)

    register.assert_called_once_with(
        "@c",
        now,
        _state.ChannelDeletionScan(window_ids={1, 2}, missing_ids={2}),
        min_deletions=settings.neurocomment.channel_backoff_min_deletions,
        base_seconds=settings.neurocomment.channel_backoff_base_seconds,
        max_seconds=settings.neurocomment.channel_backoff_max_seconds,
    )
    log.assert_not_awaited()


@pytest.mark.asyncio
async def test_backoff_trip_logs_missing_count_and_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_state, "channel_in_backoff", lambda *_a: False)
    monkeypatch.setattr(
        _sweep._seams,
        "execute_read",
        AsyncMock(return_value=CheckMessagesAliveResult(missing_ids=[1, 2])),
    )
    monkeypatch.setattr(
        _sweep, "mark_comments_deleted", AsyncMock(return_value=SimpleNamespace(comments=[]))
    )
    register = Mock(return_value=600)
    monkeypatch.setattr(_state, "register_channel_deletions", register)
    log = AsyncMock()
    monkeypatch.setattr(_sweep, "log_event", log)

    now = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
    await _sweep._sweep_channel("@c", [_comment("@c", 1), _comment("@c", 2)], now)

    register.assert_called_once_with(
        "@c",
        now,
        _state.ChannelDeletionScan(window_ids={1, 2}, missing_ids={1, 2}),
        min_deletions=settings.neurocomment.channel_backoff_min_deletions,
        base_seconds=settings.neurocomment.channel_backoff_base_seconds,
        max_seconds=settings.neurocomment.channel_backoff_max_seconds,
    )
    log.assert_awaited_once_with(
        "WARNING",
        "neurocomment_channel_backoff",
        extra={"channel": "@c", "missing": 2, "cooldown_seconds": 600},
    )


@pytest.mark.asyncio
async def test_campaign_comments_are_bucketed_to_their_own_channels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _sweep,
        "list_active_watch_channels",
        AsyncMock(return_value=SimpleNamespace(channels=["@a", "@b", "@orphan"])),
    )

    async def campaign(channel: str) -> SimpleNamespace | None:
        return None if channel == "@orphan" else SimpleNamespace(campaign_id="campaign")

    monkeypatch.setattr(_sweep, "fetch_active_campaign_for_channel", campaign)
    listed = AsyncMock(
        return_value=SimpleNamespace(comments=[_comment("@a", 1), _comment("@b", 2)])
    )
    channel = AsyncMock()
    monkeypatch.setattr(_sweep, "list_posted_comments_since", listed)
    monkeypatch.setattr(_sweep, "_sweep_channel", channel)

    await _sweep._sweep_once()

    assert listed.await_count == 1
    buckets = [
        (call.args[0], [c.comment_msg_id for c in call.args[1]]) for call in channel.await_args_list
    ]
    assert buckets == [
        ("@a", [1]),
        ("@b", [2]),
    ]


@pytest.mark.asyncio
async def test_one_campaign_read_failure_does_not_touch_other_campaigns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _sweep,
        "list_active_watch_channels",
        AsyncMock(return_value=SimpleNamespace(channels=["@a", "@b"])),
    )

    async def campaign(channel: str) -> SimpleNamespace:
        return SimpleNamespace(campaign_id=channel)

    monkeypatch.setattr(_sweep, "fetch_active_campaign_for_channel", campaign)
    listed = AsyncMock(side_effect=[RuntimeError("storage"), SimpleNamespace(comments=[])])
    monkeypatch.setattr(_sweep, "list_posted_comments_since", listed)

    with pytest.raises(RuntimeError, match="storage"):
        await _sweep._sweep_once()

    # Campaign-level storage failure is deliberately pass-fatal; the periodic outer
    # loop logs it and retries next interval, avoiding a partial view of that campaign.
    assert listed.await_count == 1
