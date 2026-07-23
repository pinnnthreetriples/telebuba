"""Channel-creation tests for the Telegram write dispatcher."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from telethon import errors
from telethon.tl.functions.channels import (
    CheckUsernameRequest,
    CreateChannelRequest,
    DeleteChannelRequest,
    UpdateUsernameRequest,
)

from core.telegram_client import execute
from schemas.telegram_actions import CreateChannel, DeleteChannel
from tests.core.telegram_client.helpers import patch_action_client as _patch_client


@pytest.mark.asyncio
async def test_create_channel_sends_broadcast_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No username → straight to CreateChannelRequest with broadcast=True."""
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            captured.append(request)
            if isinstance(request, CreateChannelRequest):
                return SimpleNamespace(chats=[SimpleNamespace(id=4242)])
            return MagicMock()

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-ch", CreateChannel(title="My channel", about="Desc"))

    assert result.status == "ok"
    assert result.action_type == "channel_create"
    creates = [r for r in captured if isinstance(r, CreateChannelRequest)]
    assert len(creates) == 1
    assert creates[0].title == "My channel"
    assert creates[0].about == "Desc"
    assert creates[0].broadcast is True
    assert creates[0].megagroup is False
    # No username → no availability probe, no username assignment.
    assert not any(isinstance(r, CheckUsernameRequest) for r in captured)
    assert not any(isinstance(r, UpdateUsernameRequest) for r in captured)
    # The new channel id rides back as an int64 STRING.
    assert result.channel_id == "4242"


@pytest.mark.asyncio
async def test_create_channel_with_username_assigns_after_precheck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            captured.append(request)
            if isinstance(request, CheckUsernameRequest):
                return True
            if isinstance(request, CreateChannelRequest):
                return SimpleNamespace(chats=[SimpleNamespace(id=987)])
            return MagicMock()

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-ch-user",
        CreateChannel(title="Public", username="my_channel"),
    )

    assert result.status == "ok"
    assert result.channel_id == "987"
    # Probe BEFORE create, username assignment AFTER create — in that order.
    kinds = [type(r).__name__ for r in captured]
    assert kinds.index("CheckUsernameRequest") < kinds.index("CreateChannelRequest")
    assert kinds.index("CreateChannelRequest") < kinds.index("UpdateUsernameRequest")
    update = next(r for r in captured if isinstance(r, UpdateUsernameRequest))
    assert update.username == "my_channel"


@pytest.mark.asyncio
async def test_create_channel_occupied_username_creates_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused handle must fail BEFORE anything is created (no orphan)."""
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            captured.append(request)
            if isinstance(request, CheckUsernameRequest):
                return False
            return MagicMock()

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-ch-occupied",
        CreateChannel(title="Public", username="taken_name"),
    )

    assert result.status == "failed"
    assert result.error_message == "channel_username_occupied"
    assert not any(isinstance(r, CreateChannelRequest) for r in captured)
    assert result.channel_id is None


@pytest.mark.asyncio
async def test_create_channel_username_failure_after_create_carries_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CHANNELS_ADMIN_PUBLIC_TOO_MUCH fires on the ASSIGNMENT, not the pre-check.

    The channel then already exists (private): the failed result must carry
    its id so the caller can adopt it instead of re-creating a duplicate, and
    NOTHING may be deleted (never auto-delete - repo data-safety rule).
    """
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            captured.append(request)
            if isinstance(request, CheckUsernameRequest):
                return True
            if isinstance(request, CreateChannelRequest):
                return SimpleNamespace(chats=[SimpleNamespace(id=987)])
            if isinstance(request, UpdateUsernameRequest):
                raise errors.ChannelsAdminPublicTooMuchError(request=None)
            return MagicMock()

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-ch-postfail",
        CreateChannel(title="Public", username="my_channel"),
    )

    assert result.status == "failed"
    assert result.error_message == "channels_admin_public_too_much"
    # The created channel id rides the FAILED result (int64 as string).
    assert result.channel_id == "987"
    assert not any(isinstance(r, DeleteChannelRequest) for r in captured)


@pytest.mark.asyncio
async def test_create_channel_without_returned_entity_surfaces_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            if isinstance(request, CreateChannelRequest):
                return SimpleNamespace(chats=[])
            return MagicMock()

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-ch-empty", CreateChannel(title="Ghost"))

    assert result.status == "failed"
    assert result.error_message == "channel_create_failed"


@pytest.mark.asyncio
async def test_create_channel_maps_channels_too_much_to_stable_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            if isinstance(request, CreateChannelRequest):
                raise errors.ChannelsTooMuchError(request=None)
            return MagicMock()

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-ch-toomuch", CreateChannel(title="One more"))

    assert result.status == "failed"
    assert result.error_message == "channels_too_much"


@pytest.mark.asyncio
async def test_create_channel_flood_wait_reaches_flood_ladder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flood errors must NOT be swallowed by the channel RPC mapping."""

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:
            raise errors.FloodWaitError(request=None, capture=33)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-ch-flood", CreateChannel(title="Flooded"))

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 33


@pytest.mark.asyncio
async def test_channel_unmapped_rpc_error_passes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:
            raise errors.ChatWriteForbiddenError(request=None)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-ch-other", CreateChannel(title="Nope"))

    assert result.status == "failed"
    assert result.error_type == "ChatWriteForbiddenError"


@pytest.mark.asyncio
async def test_create_channel_unmapped_username_failure_still_carries_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An UNMAPPED refusal on the username assignment must not lose the id.

    The channel already exists as private at that point — a raw pass-through
    (the pre-fix behaviour) dropped ``channel_id`` from the failed result and
    leaked Telethon prose; a generic stable code keeps both contracts.
    """
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            captured.append(request)
            if isinstance(request, CheckUsernameRequest):
                return True
            if isinstance(request, CreateChannelRequest):
                return SimpleNamespace(chats=[SimpleNamespace(id=987)])
            if isinstance(request, UpdateUsernameRequest):
                raise errors.ChatWriteForbiddenError(request=None)
            return MagicMock()

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-ch-postfail-unmapped",
        CreateChannel(title="Public", username="my_channel"),
    )

    assert result.status == "failed"
    assert result.error_message == "channel_username_assign_failed"
    assert result.channel_id == "987"
    assert not any(isinstance(r, DeleteChannelRequest) for r in captured)


@pytest.mark.asyncio
async def test_create_channel_flood_on_username_reaches_flood_ladder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FloodWait on the username assignment must keep its dedicated handling.

    Wrapping it into ``ChannelGatewayError`` would hide the wait-seconds from
    the flood ladder; the created (private) channel still surfaces via
    list-own-channels on the next refresh.
    """

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            if isinstance(request, CheckUsernameRequest):
                return True
            if isinstance(request, CreateChannelRequest):
                return SimpleNamespace(chats=[SimpleNamespace(id=987)])
            if isinstance(request, UpdateUsernameRequest):
                raise errors.FloodWaitError(request=None, capture=44)
            return MagicMock()

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-ch-postflood",
        CreateChannel(title="Public", username="my_channel"),
    )

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 44


@pytest.mark.asyncio
async def test_channel_action_rejects_beyond_int64_id_with_stable_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An id past int64 is ``channel_not_found``, not an OverflowError 500.

    ``get_input_entity`` must not even be called: the sqlite session lookup
    raises OverflowError (not ValueError) for such ids, which used to escape
    the guard and surface as a 500.
    """

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, _peer: object) -> object:
            msg = "must not be called for an out-of-range id"
            raise AssertionError(msg)

        async def __call__(self, _request: object) -> object:  # pragma: no cover
            return MagicMock()

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-ch-huge", DeleteChannel(channel_id=2**63))

    assert result.status == "failed"
    assert result.error_message == "channel_not_found"
