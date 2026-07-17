"""Deletion sweep invariants and partial-failure contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from schemas.neurocomment import CommentRecord
from schemas.telegram_actions import CheckMessagesAliveResult
from services.neurocomment import _state, _sweep

pytestmark = pytest.mark.usefixtures("isolate_runtime")


def _comment(message_id: int | None, *, account: str = "reader") -> CommentRecord:
    return CommentRecord(
        channel="@channel",
        post_id=message_id or 0,
        campaign_id="campaign",
        account_id=account,
        status="posted",
        comment_msg_id=message_id,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


@pytest.mark.asyncio
async def test_backoff_skips_gateway_and_persistence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_state, "channel_in_backoff", lambda *_a: True)
    read = AsyncMock()
    mark = AsyncMock()
    monkeypatch.setattr(_sweep._seams, "execute_read", read)
    monkeypatch.setattr(_sweep, "mark_comments_deleted", mark)

    await _sweep._sweep_channel("@channel", [_comment(1)], datetime.now(UTC))

    read.assert_not_awaited()
    mark.assert_not_awaited()


@pytest.mark.asyncio
async def test_null_message_ids_do_not_trigger_empty_gateway_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_state, "channel_in_backoff", lambda *_a: False)
    read = AsyncMock()
    monkeypatch.setattr(_sweep._seams, "execute_read", read)

    await _sweep._sweep_channel("@channel", [_comment(None)], datetime.now(UTC))

    read.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_exception_isolated_before_delete_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_state, "channel_in_backoff", lambda *_a: False)
    monkeypatch.setattr(
        _sweep._seams, "execute_read", AsyncMock(side_effect=RuntimeError("gateway"))
    )
    mark = AsyncMock()
    log = AsyncMock()
    monkeypatch.setattr(_sweep, "mark_comments_deleted", mark)
    monkeypatch.setattr(_sweep, "log_event", log)

    await _sweep._sweep_channel("@channel", [_comment(1)], datetime.now(UTC))

    mark.assert_not_awaited()
    log.assert_awaited_once()
    call = log.await_args
    assert call is not None
    assert call.kwargs["account_id"] == "reader"
    assert call.kwargs["extra"]["error_type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_only_gateway_missing_ids_are_persisted_and_scanned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    monkeypatch.setattr(_state, "channel_in_backoff", lambda *_a: False)
    monkeypatch.setattr(
        _sweep._seams,
        "execute_read",
        AsyncMock(return_value=CheckMessagesAliveResult(missing_ids=[2])),
    )
    mark = AsyncMock(return_value=SimpleNamespace(comments=[_comment(2)]))
    register = Mock(return_value=None)
    monkeypatch.setattr(_sweep, "mark_comments_deleted", mark)
    monkeypatch.setattr(_state, "register_channel_deletions", register)
    monkeypatch.setattr(_sweep, "log_event", AsyncMock())

    await _sweep._sweep_channel("@channel", [_comment(1), _comment(2)], now)

    mark.assert_awaited_once_with("@channel", [2])
    scan = register.call_args.args[2]
    assert scan.window_ids == {1, 2}
    assert scan.missing_ids == {2}


@pytest.mark.asyncio
async def test_sweep_once_groups_campaign_read_once_and_isolates_channels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _sweep,
        "list_active_watch_channels",
        AsyncMock(return_value=SimpleNamespace(channels=["@a", "@b"])),
    )
    monkeypatch.setattr(
        _sweep,
        "fetch_active_campaign_for_channel",
        AsyncMock(return_value=SimpleNamespace(campaign_id="campaign")),
    )
    listed = AsyncMock(return_value=SimpleNamespace(comments=[]))
    channel = AsyncMock(side_effect=[RuntimeError("a"), None])
    monkeypatch.setattr(_sweep, "list_posted_comments_since", listed)
    monkeypatch.setattr(_sweep, "_sweep_channel", channel)
    monkeypatch.setattr(_sweep, "log_event", AsyncMock())

    await _sweep._sweep_once()

    assert listed.await_count == 1
    assert [call.args[0] for call in channel.await_args_list] == ["@a", "@b"]
