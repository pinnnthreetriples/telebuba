"""Mutation operations for an account-owned channel post."""

from __future__ import annotations

import pytest

from schemas.telegram_actions import ActionResult, DeleteChannelPost, EditChannelPost
from services.accounts import delete_account_channel_post, edit_account_channel_post


def _patch_collaborators(
    monkeypatch: pytest.MonkeyPatch,
    *,
    action_type: str,
) -> tuple[list[object], list[tuple[str, str, str | None, dict[str, object] | None]]]:
    actions: list[object] = []
    events: list[tuple[str, str, str | None, dict[str, object] | None]] = []

    async def execute(account_id: str, action: object) -> ActionResult:
        assert account_id == "acc-1"
        actions.append(action)
        return ActionResult(status="ok", action_type=action_type, account_id=account_id)

    async def log_event(
        level: str,
        event: str,
        *,
        account_id: str | None = None,
        extra: dict[str, object] | None = None,
    ) -> None:
        events.append((level, event, account_id, extra))

    monkeypatch.setattr("services.accounts.channel_posts.execute", execute)
    monkeypatch.setattr("services.accounts.channel_posts.log_event", log_event)
    return actions, events


@pytest.mark.asyncio
async def test_edit_post_executes_and_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    actions, events = _patch_collaborators(monkeypatch, action_type="channel_post_edit")

    await edit_account_channel_post("acc-1", 42, 10, text="fixed")

    assert len(actions) == 1
    action = actions[0]
    assert isinstance(action, EditChannelPost)
    assert action.channel_id == 42
    assert action.post_id == 10
    assert action.text == "fixed"
    assert events == [
        (
            "INFO",
            "account_channel_post_edited",
            "acc-1",
            {"channel_id": 42, "post_id": 10},
        ),
    ]


@pytest.mark.asyncio
async def test_delete_post_executes_and_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    actions, events = _patch_collaborators(monkeypatch, action_type="channel_post_delete")

    await delete_account_channel_post("acc-1", 42, 10)

    assert len(actions) == 1
    action = actions[0]
    assert isinstance(action, DeleteChannelPost)
    assert action.channel_id == 42
    assert action.post_id == 10
    assert events == [
        (
            "INFO",
            "account_channel_post_deleted",
            "acc-1",
            {"channel_id": 42, "post_id": 10},
        ),
    ]
