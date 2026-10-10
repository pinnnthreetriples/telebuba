"""Profile-pin (personal channel) tests for the channel write dispatcher."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from telethon import errors
from telethon.tl.functions.account import UpdatePersonalChannelRequest
from telethon.tl.functions.channels import (
    CreateChannelRequest,
    DeleteChannelRequest,
    UpdateUsernameRequest,
)
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import InputChannelEmpty

from core.telegram_client import execute
from schemas.channels import ChannelCreateRequest
from schemas.telegram_actions import CreateChannel, EditChannel
from tests.core.telegram_client.helpers import patch_action_client as _patch_client


class _CreateClient:
    """Creates channel 4300; ``pin_error`` is raised by the pin call."""

    def __init__(self, pin_error: Exception | None = None) -> None:
        self.captured: list[object] = []
        self.pin_error = pin_error

    async def connect(self) -> None:
        return None

    async def __call__(self, request: object) -> object:
        self.captured.append(request)
        if isinstance(request, CreateChannelRequest):
            return SimpleNamespace(chats=[SimpleNamespace(id=4300)])
        if isinstance(request, UpdatePersonalChannelRequest) and self.pin_error is not None:
            raise self.pin_error
        return True


class _EditClient:
    """Resolves any channel; the account's personal channel is ``pinned_id``."""

    def __init__(self, pinned_id: int | None) -> None:
        self.captured: list[object] = []
        self.entity = MagicMock(name="input-channel")
        self.pinned_id = pinned_id

    async def connect(self) -> None:
        return None

    async def get_input_entity(self, _peer: object) -> object:
        return self.entity

    async def __call__(self, request: object) -> object:
        self.captured.append(request)
        if isinstance(request, GetFullUserRequest):
            return SimpleNamespace(full_user=SimpleNamespace(personal_channel_id=self.pinned_id))
        return True


def _pins(captured: list[object]) -> list[UpdatePersonalChannelRequest]:
    return [r for r in captured if isinstance(r, UpdatePersonalChannelRequest)]


@pytest.mark.asyncio
async def test_create_pins_after_the_username_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Telegram pins only a public channel, so the pin follows the username."""
    client = _CreateClient()
    _patch_client(monkeypatch, client)

    result = await execute(
        "acc-pin",
        CreateChannel(title="Mine", username="mine_chan", pinned_to_profile=True),
    )

    assert result.status == "ok"
    assert result.channel_id == "4300"
    kinds = [type(r) for r in client.captured]
    assert kinds.index(UpdateUsernameRequest) < kinds.index(UpdatePersonalChannelRequest)
    assert len(_pins(client.captured)) == 1


@pytest.mark.asyncio
async def test_create_without_pin_sends_no_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _CreateClient()
    _patch_client(monkeypatch, client)

    result = await execute("acc-nopin", CreateChannel(title="Mine", username="mine_chan"))

    assert result.status == "ok"
    assert _pins(client.captured) == []


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (
            errors.BadRequestError(request=None, message="PUBLIC_BROADCAST_EXPECTED"),
            "channel_pin_needs_public",
        ),
        (errors.ChatAdminRequiredError(request=None), "channel_pin_failed"),
    ],
)
@pytest.mark.asyncio
async def test_create_pin_refusal_carries_the_id(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    code: str,
) -> None:
    """The channel already exists — the id rides the failure, nothing is rolled back."""
    client = _CreateClient(pin_error=error)
    _patch_client(monkeypatch, client)

    result = await execute(
        "acc-pinfail",
        CreateChannel(title="Mine", username="mine_chan", pinned_to_profile=True),
    )

    assert result.status == "failed"
    assert result.error_message == code
    assert result.channel_id == "4300"
    assert not any(isinstance(r, DeleteChannelRequest) for r in client.captured)


@pytest.mark.asyncio
async def test_create_pin_flood_reaches_flood_ladder(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(
        monkeypatch, _CreateClient(pin_error=errors.FloodWaitError(request=None, capture=33))
    )

    result = await execute(
        "acc-pinflood",
        CreateChannel(title="Mine", username="mine_chan", pinned_to_profile=True),
    )

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 33


def test_pin_without_username_is_rejected() -> None:
    with pytest.raises(ValidationError, match="pinned_to_profile requires a username"):
        CreateChannel(title="Mine", pinned_to_profile=True)
    with pytest.raises(ValidationError, match="pinned_to_profile requires a username"):
        ChannelCreateRequest(title="Mine", pinned_to_profile=True)


@pytest.mark.asyncio
async def test_edit_pin_pins_the_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _EditClient(pinned_id=None)
    _patch_client(monkeypatch, client)

    result = await execute("acc-editpin", EditChannel(channel_id=55, pinned_to_profile=True))

    assert result.status == "ok"
    pins = _pins(client.captured)
    assert len(pins) == 1
    assert pins[0].channel is client.entity


@pytest.mark.asyncio
async def test_edit_unpin_clears_this_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _EditClient(pinned_id=55)
    _patch_client(monkeypatch, client)

    result = await execute("acc-unpin", EditChannel(channel_id=55, pinned_to_profile=False))

    assert result.status == "ok"
    pins = _pins(client.captured)
    assert len(pins) == 1
    assert isinstance(pins[0].channel, InputChannelEmpty)


@pytest.mark.asyncio
async def test_edit_unpin_leaves_another_channels_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale editor must not take down the pin of a different channel."""
    client = _EditClient(pinned_id=99)
    _patch_client(monkeypatch, client)

    result = await execute("acc-unpin-other", EditChannel(channel_id=55, pinned_to_profile=False))

    assert result.status == "ok"
    assert _pins(client.captured) == []


@pytest.mark.asyncio
async def test_edit_pin_of_a_private_channel_says_why(monkeypatch: pytest.MonkeyPatch) -> None:
    """The channel went private elsewhere: the refusal is the stable code, no id."""

    class _PrivateClient(_EditClient):
        async def __call__(self, request: object) -> object:
            if isinstance(request, UpdatePersonalChannelRequest):
                raise errors.BadRequestError(request=None, message="PUBLIC_BROADCAST_EXPECTED")
            return await super().__call__(request)

    _patch_client(monkeypatch, _PrivateClient(pinned_id=None))

    result = await execute("acc-editpriv", EditChannel(channel_id=55, pinned_to_profile=True))

    assert result.status == "failed"
    assert result.error_message == "channel_pin_needs_public"
    assert result.channel_id is None
