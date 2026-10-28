"""The write-rights read asked of a group itself — the chat a broadcast writes into."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from telethon.tl.functions.channels import GetFullChannelRequest, GetParticipantRequest
from telethon.tl.types import ChannelParticipantBanned, Chat, ChatBannedRights, PeerUser

from core.telegram_client import execute_read
from schemas.telegram_actions_rights import CheckWriteRights, WriteRightsResult
from tests.core.telegram_client.helpers import patch_read_client as _patch_client


def _client(entity: object, participant: object = None) -> tuple[object, list[object]]:
    seen: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_entity(self, peer: object) -> object:
            seen.append(peer)
            if entity is None:
                raise ValueError(peer)
            return entity

        async def __call__(self, request: object) -> object:
            seen.append(request)
            assert isinstance(request, GetParticipantRequest)
            return MagicMock(participant=participant)

    return FakeClient(), seen


def _muted_self() -> object:
    return ChannelParticipantBanned(
        peer=PeerUser(1),
        kicked_by=2,
        date=datetime.now(UTC),
        banned_rights=ChatBannedRights(until_date=None, send_messages=True),
    )


@pytest.mark.asyncio
async def test_in_chat_reads_the_group_itself_not_a_linked_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    group = MagicMock(id=555, default_banned_rights=ChatBannedRights(until_date=None))
    client, seen = _client(group, _muted_self())
    _patch_client(monkeypatch, client)

    result = await execute_read("acc-x", CheckWriteRights(channel="555", in_chat=True))

    assert isinstance(result, WriteRightsResult)
    assert result.scope == "self_only"
    # The raw id goes in as an int, and no linked-group lookup is paid for.
    assert seen[0] == 555
    assert not [request for request in seen if isinstance(request, GetFullChannelRequest)]


@pytest.mark.asyncio
async def test_a_basic_group_cannot_mute_one_member(monkeypatch: pytest.MonkeyPatch) -> None:
    group = Chat(
        id=7,
        title="g",
        photo=None,  # ty: ignore[invalid-argument-type]
        participants_count=3,
        date=datetime.now(UTC),
        version=1,
        default_banned_rights=ChatBannedRights(until_date=None),
    )
    client, seen = _client(group)
    _patch_client(monkeypatch, client)

    result = await execute_read("acc-x", CheckWriteRights(channel="@group", in_chat=True))

    assert isinstance(result, WriteRightsResult)
    assert result.scope == "none"
    assert not [request for request in seen if isinstance(request, GetParticipantRequest)]


@pytest.mark.asyncio
async def test_an_unknown_chat_is_no_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _seen = _client(None)
    _patch_client(monkeypatch, client)

    result = await execute_read("acc-x", CheckWriteRights(channel="@gone", in_chat=True))

    assert isinstance(result, WriteRightsResult)
    assert (result.scope, result.reason) == ("unknown", "chat_not_found")
