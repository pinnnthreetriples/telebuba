"""Channel membership tests for the typed-action dispatcher."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from telethon import errors
from telethon.tl.functions.channels import (
    GetFullChannelRequest,
    JoinChannelRequest,
    LeaveChannelRequest,
)

from core.telegram_client import execute
from schemas.telegram_actions import (
    JoinChannel,
    JoinDiscussionGroup,
    LeaveChannel,
)
from tests.core.telegram_client.helpers import patch_action_client as _patch_client


@pytest.mark.asyncio
async def test_execute_join_channel_dispatches_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> None:
            captured.append(request)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-1", JoinChannel(channel="@news"))

    assert result.status == "ok"
    assert result.action_type == "join_channel"
    assert result.account_id == "acc-1"
    assert any(isinstance(req, JoinChannelRequest) for req in captured)


@pytest.mark.asyncio
async def test_execute_join_channel_with_plus_hash_dispatches_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> None:
            captured.append(request)

    _patch_client(monkeypatch, FakeClient())

    # The action logic uses extract_invite_hash which parses +HASH correctly.
    result = await execute("acc-2", JoinChannel(channel="+AbC-123_456xYz"))

    assert result.status == "ok"
    assert len(captured) == 1
    req = captured[0]
    assert req.__class__.__name__ == "ImportChatInviteRequest"
    assert getattr(req, "hash", "") == "AbC-123_456xYz"


@pytest.mark.asyncio
async def test_execute_join_channel_with_joinchat_dispatches_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> None:
            captured.append(request)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-3", JoinChannel(channel="joinchat/AbC-123_456xYz"))

    assert result.status == "ok"
    assert len(captured) == 1
    req = captured[0]
    assert req.__class__.__name__ == "ImportChatInviteRequest"
    assert getattr(req, "hash", "") == "AbC-123_456xYz"


@pytest.mark.asyncio
async def test_execute_join_channel_already_participant_returns_already_participant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> None:
            raise errors.UserAlreadyParticipantError(request=None)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-already", JoinChannel(channel="@already"))

    # Distinct from a real join so the caller can skip the rolling-24h join cap.
    assert result.status == "already_participant"
    assert result.action_type == "join_channel"


@pytest.mark.asyncio
async def test_execute_leave_channel_already_participant_does_not_swallow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> None:
            raise errors.UserAlreadyParticipantError(request=None)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc", LeaveChannel(channel="@hot"))

    assert result.status == "failed"
    assert result.error_type == "UserAlreadyParticipantError"


@pytest.mark.asyncio
async def test_execute_leave_channel_dispatches_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> None:
            captured.append(request)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-2", LeaveChannel(channel="@news"))

    assert result.status == "ok"
    assert result.action_type == "leave_channel"
    assert any(isinstance(req, LeaveChannelRequest) for req in captured)


def _chat_full(linked_chat_id: int | None, *, chat_ids: tuple[int, ...]) -> MagicMock:
    """Build a fake ``messages.ChatFull`` with a ``full_chat`` + ``chats`` list."""
    full = MagicMock()
    full.full_chat = MagicMock(linked_chat_id=linked_chat_id)
    full.chats = [MagicMock(id=cid) for cid in chat_ids]
    return full


@pytest.mark.asyncio
async def test_join_discussion_group_joins_resolved_entity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []
    linked_entity = MagicMock(id=4423)

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            captured.append(request)
            if isinstance(request, GetFullChannelRequest):
                full = MagicMock()
                full.full_chat = MagicMock(linked_chat_id=4423)
                full.chats = [MagicMock(id=999), linked_entity]
                return full
            return None

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-1", JoinDiscussionGroup(channel="@news"))

    assert result.status == "ok"
    assert result.action_type == "join_discussion_group"
    join_reqs = [r for r in captured if isinstance(r, JoinChannelRequest)]
    assert len(join_reqs) == 1
    # joined the resolved linked entity, not the parent channel
    assert join_reqs[0].channel is linked_entity


@pytest.mark.asyncio
async def test_join_discussion_group_already_participant_reports_already_participant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            if isinstance(request, GetFullChannelRequest):
                return _chat_full(4423, chat_ids=(4423,))
            raise errors.UserAlreadyParticipantError(request=None)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-2", JoinDiscussionGroup(channel="@news"))

    assert result.status == "already_participant"
    assert result.action_type == "join_discussion_group"


@pytest.mark.asyncio
async def test_join_discussion_group_no_linked_group_classified_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            assert isinstance(request, GetFullChannelRequest)
            return _chat_full(None, chat_ids=())

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-3", JoinDiscussionGroup(channel="@silent"))

    assert result.status == "failed"
    assert result.error_type == "ValueError"
