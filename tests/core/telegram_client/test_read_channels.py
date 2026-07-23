"""Linked discussion and channel-state read tests."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from telethon import errors

from core.telegram_client import (
    execute_read,
)
from schemas.telegram_actions import (
    BanCheckResult,
    CheckBannedInChannel,
    CheckMessagesAlive,
    CheckMessagesAliveResult,
    GetLinkedDiscussionGroup,
    LinkedDiscussionGroupResult,
)
from tests.core.telegram_client.helpers import patch_read_client as _patch_client


@pytest.mark.asyncio
async def test_get_linked_discussion_group_present(monkeypatch: pytest.MonkeyPatch) -> None:
    from telethon.tl.functions.channels import GetFullChannelRequest  # noqa: PLC0415

    requested: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            requested.append(request)
            return MagicMock(full_chat=MagicMock(linked_chat_id=-100123))

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-linked", GetLinkedDiscussionGroup(channel="@news"))

    assert isinstance(result, LinkedDiscussionGroupResult)
    assert result.linked_chat_id == -100123
    assert result.comments_enabled is True
    assert any(isinstance(req, GetFullChannelRequest) for req in requested)


@pytest.mark.asyncio
async def test_get_linked_discussion_group_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:
            return MagicMock(full_chat=MagicMock(linked_chat_id=None))

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-nolink", GetLinkedDiscussionGroup(channel="@nocomments"))

    assert isinstance(result, LinkedDiscussionGroupResult)
    assert result.linked_chat_id is None
    assert result.comments_enabled is False


@pytest.mark.asyncio
async def test_check_messages_alive_reports_deleted_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ``get_messages`` ``None`` for an id means that comment was deleted/gone."""
    from telethon.tl.functions.channels import GetFullChannelRequest  # noqa: PLC0415

    group = MagicMock(id=999)
    read_calls: list[list[int]] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            assert isinstance(request, GetFullChannelRequest)
            return MagicMock(full_chat=MagicMock(linked_chat_id=999), chats=[group])

        async def get_messages(self, entity: object, *, ids: list[int]) -> list[object | None]:
            assert entity is group  # reads the resolved linked discussion group
            read_calls.append(ids)
            return [None if mid == 2 else MagicMock() for mid in ids]

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-x", CheckMessagesAlive(channel="@news", message_ids=[1, 2, 3]))

    assert isinstance(result, CheckMessagesAliveResult)
    assert result.missing_ids == [2]
    assert read_calls == [[1, 2, 3]]


@pytest.mark.asyncio
async def test_check_messages_alive_no_linked_group_reports_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Comments disabled / unlinked → can't verify, so report nothing gone."""

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:
            return MagicMock(full_chat=MagicMock(linked_chat_id=None), chats=[])

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-x", CheckMessagesAlive(channel="@news", message_ids=[1, 2]))

    assert isinstance(result, CheckMessagesAliveResult)
    assert result.missing_ids == []


@pytest.mark.asyncio
async def test_check_messages_alive_group_absent_from_chats_resolves_via_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Linked group missing from ChatFull → resolve off the warm cache and still read ids."""
    group = MagicMock(id=999)

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, _peer: object) -> object:
            return group

        async def __call__(self, _request: object) -> object:
            return MagicMock(full_chat=MagicMock(linked_chat_id=999), chats=[MagicMock(id=111)])

        async def get_messages(self, entity: object, *, ids: list[int]) -> list[object | None]:
            assert entity is group  # resolved via the cache fallback
            return [None if mid == 2 else MagicMock() for mid in ids]

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-x", CheckMessagesAlive(channel="@news", message_ids=[1, 2]))

    assert isinstance(result, CheckMessagesAliveResult)
    assert result.missing_ids == [2]


@pytest.mark.asyncio
async def test_check_messages_alive_unresolvable_group_reports_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Absent from ChatFull AND the cache can't resolve it → no false positives."""

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, _peer: object) -> object:
            raise ValueError

        async def __call__(self, _request: object) -> object:
            return MagicMock(full_chat=MagicMock(linked_chat_id=999), chats=[MagicMock(id=111)])

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-x", CheckMessagesAlive(channel="@news", message_ids=[1]))

    assert isinstance(result, CheckMessagesAliveResult)
    assert result.missing_ids == []


def _ban_client(
    participant: object | None,
    *,
    linked: int | None = 999,
    in_chats: bool = True,
    resolvable: bool = True,
) -> object:
    """A FakeClient answering GetFullChannel then GetParticipant for the ban probe.

    ``participant`` None → raise UserNotParticipantError; otherwise return it
    wrapped as ``.participant``. ``linked`` None → channel has no linked group.
    ``in_chats`` False → the linked group is absent from ``ChatFull.chats`` (so the
    probe must fall back to ``get_input_entity``); ``resolvable`` False → that
    fallback also fails.
    """
    from telethon.tl.functions.channels import (  # noqa: PLC0415
        GetFullChannelRequest,
        GetParticipantRequest,
    )

    group = MagicMock(id=999)

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, _peer: object) -> object:
            if not resolvable:
                raise ValueError
            return group

        async def __call__(self, request: object) -> object:
            if isinstance(request, GetFullChannelRequest):
                chats = [group] if (linked is not None and in_chats) else []
                return MagicMock(full_chat=MagicMock(linked_chat_id=linked), chats=chats)
            assert isinstance(request, GetParticipantRequest)
            if participant is None:
                raise errors.UserNotParticipantError(request=None)
            return MagicMock(participant=participant)

    return FakeClient()


@pytest.mark.asyncio
async def test_check_banned_muted_participant_is_restricted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ChannelParticipantBanned with send_messages restricted → restricted."""
    from telethon.tl.types import (  # noqa: PLC0415
        ChannelParticipantBanned,
        ChatBannedRights,
        PeerUser,
    )

    banned = ChannelParticipantBanned(
        peer=PeerUser(1),
        kicked_by=2,
        date=datetime.now(UTC),
        banned_rights=ChatBannedRights(until_date=0, send_messages=True),  # ty: ignore[invalid-argument-type]
    )
    _patch_client(monkeypatch, _ban_client(banned))

    result = await execute_read("acc-x", CheckBannedInChannel(channel="@news"))

    assert isinstance(result, BanCheckResult)
    assert result.state == "restricted"


@pytest.mark.asyncio
async def test_check_banned_kicked_participant_is_not_member(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """view_messages restricted = kicked out entirely → not_member."""
    from telethon.tl.types import (  # noqa: PLC0415
        ChannelParticipantBanned,
        ChatBannedRights,
        PeerUser,
    )

    kicked = ChannelParticipantBanned(
        peer=PeerUser(1),
        kicked_by=2,
        date=datetime.now(UTC),
        banned_rights=ChatBannedRights(until_date=0, view_messages=True),  # ty: ignore[invalid-argument-type]
    )
    _patch_client(monkeypatch, _ban_client(kicked))

    result = await execute_read("acc-x", CheckBannedInChannel(channel="@news"))

    assert isinstance(result, BanCheckResult)
    assert result.state == "not_member"


@pytest.mark.asyncio
async def test_check_banned_normal_participant_can_send(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any non-banned participant type → can_send."""
    _patch_client(monkeypatch, _ban_client(MagicMock()))

    result = await execute_read("acc-x", CheckBannedInChannel(channel="@news"))

    assert isinstance(result, BanCheckResult)
    assert result.state == "can_send"


@pytest.mark.asyncio
async def test_check_banned_not_participant_is_not_member(monkeypatch: pytest.MonkeyPatch) -> None:
    """UserNotParticipantError (kicked / never joined) → not_member."""
    _patch_client(monkeypatch, _ban_client(None))

    result = await execute_read("acc-x", CheckBannedInChannel(channel="@news"))

    assert isinstance(result, BanCheckResult)
    assert result.state == "not_member"


@pytest.mark.asyncio
async def test_check_banned_no_linked_group_is_comments_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No linked discussion group / comments off → can't check → comments_disabled."""
    _patch_client(monkeypatch, _ban_client(MagicMock(), linked=None))

    result = await execute_read("acc-x", CheckBannedInChannel(channel="@news"))

    assert isinstance(result, BanCheckResult)
    assert result.state == "comments_disabled"


@pytest.mark.asyncio
async def test_check_banned_group_absent_from_chats_resolves_via_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Linked group missing from ChatFull.chats → resolve off the warm cache, not 'disabled'."""
    _patch_client(monkeypatch, _ban_client(MagicMock(), in_chats=False))

    result = await execute_read("acc-x", CheckBannedInChannel(channel="@news"))

    assert isinstance(result, BanCheckResult)
    assert result.state == "can_send"


@pytest.mark.asyncio
async def test_check_banned_group_unresolvable_is_comments_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Absent from chats AND the cache can't resolve it → honest comments_disabled."""
    _patch_client(monkeypatch, _ban_client(MagicMock(), in_chats=False, resolvable=False))

    result = await execute_read("acc-x", CheckBannedInChannel(channel="@news"))

    assert isinstance(result, BanCheckResult)
    assert result.state == "comments_disabled"
