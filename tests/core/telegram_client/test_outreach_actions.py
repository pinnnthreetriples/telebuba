"""Gateway tests for the outreach family: "typing…" before a bulk first message."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from telethon import errors
from telethon.tl.functions.messages import SetTypingRequest
from telethon.tl.types import InputPeerUser, SendMessageTypingAction

from core.telegram_client import execute
from schemas.telegram_actions_outreach import OutreachSetTyping
from tests.core.telegram_client.helpers import patch_action_client


class _Client:
    """Records every request and every peer it was asked to resolve."""

    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.peers: list[object] = []
        self.requests: list[object] = []

    async def connect(self) -> None:
        return None

    async def get_input_entity(self, peer: object) -> object:
        self.peers.append(peer)
        return InputPeerUser(user_id=42, access_hash=7)

    async def __call__(self, request: object) -> object:
        if self.failure is not None:
            raise self.failure
        self.requests.append(request)
        return MagicMock()


@pytest.mark.asyncio
@pytest.mark.parametrize(("recipient", "peer"), [("42", 42), ("someone", "someone")])
async def test_typing_is_shown_to_the_recipient_as_the_send_addresses_it(
    monkeypatch: pytest.MonkeyPatch, recipient: str, peer: object
) -> None:
    client = _Client()
    patch_action_client(monkeypatch, client)

    result = await execute("acc", OutreachSetTyping(recipient=recipient), domain="bulk_messages")

    assert result.status == "ok"
    assert client.peers == [peer]
    [request] = client.requests
    assert isinstance(request, SetTypingRequest)
    assert isinstance(request.action, SendMessageTypingAction)


@pytest.mark.asyncio
async def test_a_flood_on_typing_is_classified_by_the_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_action_client(monkeypatch, _Client(errors.FloodWaitError(request=None, capture=17)))

    result = await execute("acc", OutreachSetTyping(recipient="42"), domain="bulk_messages")

    assert (result.status, result.flood_wait_seconds) == ("flood_wait", 17)


@pytest.mark.asyncio
async def test_any_other_typing_failure_is_a_plain_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_action_client(monkeypatch, _Client(errors.UserPrivacyRestrictedError(request=None)))

    result = await execute("acc", OutreachSetTyping(recipient="42"), domain="bulk_messages")

    assert (result.status, result.error_type) == ("failed", "UserPrivacyRestrictedError")
