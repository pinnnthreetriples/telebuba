"""Onboarding progress and partial-failure contracts."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from schemas.telegram_actions import LinkedDiscussionGroupResult
from services.neurocomment import onboarding

pytestmark = pytest.mark.usefixtures("isolate_onboarding")


@pytest.mark.asyncio
async def test_resolve_falls_through_failed_account_and_reports_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    safe = AsyncMock(
        side_effect=[
            None,
            LinkedDiscussionGroupResult(linked_chat_id=123, comments_enabled=True),
        ]
    )
    monkeypatch.setattr(onboarding, "_safe_resolve", safe)
    events = []
    outcomes = []

    group_id = await onboarding._resolve_group_for_join(
        ["bad", "good"], "@channel", outcomes, events.append
    )

    assert group_id == 123
    assert [call.args[0] for call in safe.await_args_list] == ["bad", "good"]
    assert outcomes == []
    assert [(event.code, event.channel) for event in events] == [("channel_resolved", "@channel")]


@pytest.mark.asyncio
async def test_all_resolve_failures_create_one_outcome_per_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(onboarding, "_safe_resolve", AsyncMock(return_value=None))
    outcomes = []
    events = []

    result = await onboarding._resolve_group_for_join(
        ["a", "b"], "@channel", outcomes, events.append
    )

    assert result is None
    assert [(item.account_id, item.state, item.reason) for item in outcomes] == [
        ("a", "failed", "resolve_failed"),
        ("b", "failed", "resolve_failed"),
    ]
    assert [event.code for event in events] == ["channel_resolve_failed"]


@pytest.mark.asyncio
async def test_comments_off_fans_out_without_join(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        onboarding,
        "_safe_resolve",
        AsyncMock(
            return_value=LinkedDiscussionGroupResult(linked_chat_id=None, comments_enabled=False)
        ),
    )
    outcomes = []
    events = []

    result = await onboarding._resolve_group_for_join(["a", "b"], "@off", outcomes, events.append)

    assert result is None
    assert [(item.account_id, item.state) for item in outcomes] == [
        ("a", "comments_off"),
        ("b", "comments_off"),
    ]
    assert [(event.code, event.channel) for event in events] == [("channel_comments_off", "@off")]


@pytest.mark.asyncio
async def test_spam_probe_failure_isolated_and_progressed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refresh = AsyncMock(side_effect=[RuntimeError("offline"), None])
    log = AsyncMock()
    monkeypatch.setattr(onboarding._seams, "refresh_spam_status", refresh)
    monkeypatch.setattr(onboarding, "log_event", log)
    events = []

    await onboarding._probe_account_spam(["a", "b"], events.append)

    assert refresh.await_count == 2
    assert [(e.code, e.account_id) for e in events] == [
        ("spam_probe_started", "a"),
        ("spam_probe_failed", "a"),
        ("spam_probe_started", "b"),
    ]
    log.assert_awaited_once_with(
        "WARNING",
        "neurocomment_onboard_spam_probe_failed",
        account_id="a",
        extra={"error_type": "RuntimeError"},
    )
