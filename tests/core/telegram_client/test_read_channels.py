"""Linked discussion and channel-state read tests."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from telethon import errors

from core.telegram_client import (
    TelegramReadError,
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
from schemas.telegram_actions_rights import CheckWriteRights, WriteRightsResult
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


def _rights_client(
    participant: object,
    *,
    default_banned_rights: object = None,
    linked: int | None = 999,
) -> tuple[object, list[object]]:
    """A FakeClient for the write-rights probe, plus the requests it actually received.

    ``default_banned_rights`` is passed EXPLICITLY: a bare ``MagicMock`` group would
    auto-create a truthy ``send_messages`` and read every chat as closed to everyone. The
    request list is what pins "the chat-wide answer costs no participant read".
    """
    from telethon.tl.functions.channels import (  # noqa: PLC0415
        GetFullChannelRequest,
        GetParticipantRequest,
    )

    group = MagicMock(id=999, default_banned_rights=default_banned_rights)
    seen: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, _peer: object) -> object:
            return group

        async def __call__(self, request: object) -> object:
            seen.append(request)
            if isinstance(request, GetFullChannelRequest):
                chats = [group] if linked is not None else []
                return MagicMock(full_chat=MagicMock(linked_chat_id=linked), chats=chats)
            assert isinstance(request, GetParticipantRequest)
            if participant is None:
                raise errors.UserNotParticipantError(request=None)
            return MagicMock(participant=participant)

    return FakeClient(), seen


def _open_rights() -> object:
    """Default rights that forbid nothing — the ordinary group everyone may write in."""
    from telethon.tl.types import ChatBannedRights  # noqa: PLC0415

    return ChatBannedRights(until_date=None)


def _muted_self(until: object) -> object:
    """Our own participant record with ``send_messages`` revoked until ``until``."""
    from telethon.tl.types import (  # noqa: PLC0415
        ChannelParticipantBanned,
        ChatBannedRights,
        PeerUser,
    )

    return ChannelParticipantBanned(
        peer=PeerUser(1),
        kicked_by=2,
        date=datetime.now(UTC),
        banned_rights=ChatBannedRights(until_date=until, send_messages=True),  # ty: ignore[invalid-argument-type]
    )


@pytest.mark.asyncio
async def test_write_rights_chat_wide_mute_is_everyone_and_skips_the_participant_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A group whose DEFAULT rights revoke send_messages is closed to all — nobody's fault.

    Checked before our own record and short-circuiting it, because a read-only group leaves
    that record untouched: reading ours first would report a channel-wide switch as a
    personal mute, which is the confusion this action exists to end.
    """
    from telethon.tl.functions.channels import GetParticipantRequest  # noqa: PLC0415
    from telethon.tl.types import ChatBannedRights  # noqa: PLC0415

    closed = ChatBannedRights(until_date=None, send_messages=True)
    client, seen = _rights_client(MagicMock(), default_banned_rights=closed)
    _patch_client(monkeypatch, client)

    result = await execute_read("acc-x", CheckWriteRights(channel="@news"))

    assert isinstance(result, WriteRightsResult)
    assert (result.scope, result.muted_until) == ("everyone", None)
    assert not [request for request in seen if isinstance(request, GetParticipantRequest)]


@pytest.mark.asyncio
async def test_write_rights_own_record_muted_is_self_only_with_its_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only WE are muted, and Telegram says until when — the answer that buys a wait."""
    until = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    client, _seen = _rights_client(_muted_self(until), default_banned_rights=_open_rights())
    _patch_client(monkeypatch, client)

    result = await execute_read("acc-x", CheckWriteRights(channel="@news"))

    assert isinstance(result, WriteRightsResult)
    assert (result.scope, result.muted_until) == ("self_only", until.isoformat())


@pytest.mark.asyncio
async def test_write_rights_permanent_mute_carries_no_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forever is ``until_date=0``, which Telethon's date reader hands back as no date.

    Reported as ``None`` rather than invented as some far-future stamp: the caller is what
    bounds an unbounded wait, and it can only do that if it can tell there is no date.
    """
    client, _seen = _rights_client(_muted_self(None), default_banned_rights=_open_rights())
    _patch_client(monkeypatch, client)

    result = await execute_read("acc-x", CheckWriteRights(channel="@news"))

    assert isinstance(result, WriteRightsResult)
    assert (result.scope, result.muted_until) == ("self_only", None)


@pytest.mark.asyncio
async def test_write_rights_unrestricted_member_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rights permit writing: the refusal came from something else, so no mute is claimed."""
    client, _seen = _rights_client(MagicMock(), default_banned_rights=_open_rights())
    _patch_client(monkeypatch, client)

    result = await execute_read("acc-x", CheckWriteRights(channel="@news"))

    assert isinstance(result, WriteRightsResult)
    assert result.scope == "none"


@pytest.mark.asyncio
async def test_write_rights_kicked_record_is_not_a_mute(monkeypatch: pytest.MonkeyPatch) -> None:
    """Telegram revokes every right at once on a ban, ``send_messages`` included.

    Without reading ``view_messages`` first, a pair that is OUT of the chat would come back
    muted and be parked waiting out an expiry that means nothing to it.
    """
    from telethon.tl.types import (  # noqa: PLC0415
        ChannelParticipantBanned,
        ChatBannedRights,
        PeerUser,
    )

    kicked = ChannelParticipantBanned(
        peer=PeerUser(1),
        kicked_by=2,
        date=datetime.now(UTC),
        banned_rights=ChatBannedRights(until_date=None, view_messages=True, send_messages=True),
    )
    client, _seen = _rights_client(kicked, default_banned_rights=_open_rights())
    _patch_client(monkeypatch, client)

    result = await execute_read("acc-x", CheckWriteRights(channel="@news"))

    assert isinstance(result, WriteRightsResult)
    assert (result.scope, result.reason) == ("unknown", "not_member")


@pytest.mark.asyncio
async def test_write_rights_no_linked_group_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing to read rights off → an honest unknown, never a verdict."""
    client, _seen = _rights_client(MagicMock(), linked=None)
    _patch_client(monkeypatch, client)

    result = await execute_read("acc-x", CheckWriteRights(channel="@news"))

    assert isinstance(result, WriteRightsResult)
    assert (result.scope, result.reason) == ("unknown", "no_linked_group")


@pytest.mark.asyncio
async def test_write_rights_not_a_participant_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """No participant record of our own → no mute to read; the kick branch owns this."""
    client, _seen = _rights_client(None, default_banned_rights=_open_rights())
    _patch_client(monkeypatch, client)

    result = await execute_read("acc-x", CheckWriteRights(channel="@news"))

    assert isinstance(result, WriteRightsResult)
    assert (result.scope, result.reason) == ("unknown", "not_member")


@pytest.mark.asyncio
async def test_write_rights_rpc_failure_collapses_to_the_shared_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing probe rides the gateway's own error convention: ``RPC: <ClassName>``.

    The reason stays machine-readable and content-free, which is what lets the caller carry
    it through as "we could not tell" instead of reading a verdict out of a message.
    """

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:
            raise errors.ChatAdminRequiredError(request=None)

    _patch_client(monkeypatch, FakeClient())

    with pytest.raises(TelegramReadError) as exc_info:
        await execute_read("acc-x", CheckWriteRights(channel="@news"))

    assert exc_info.value.reason == "RPC: ChatAdminRequiredError"
