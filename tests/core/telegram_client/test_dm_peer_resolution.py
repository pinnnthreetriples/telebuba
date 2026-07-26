"""Peer resolution for direct messages — the cold-session path and its refusals.

Mirrors ``core.telegram_client._dm``. The distinction these pin is permanent vs
transient: only a peer we can never address may become ``DmPeerUnresolvedError``,
because the caller treats that as "give up on this pair".
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from telethon import errors

from core.config import settings
from core.telegram_client import execute
from schemas.telegram_actions import SendDirectMessage
from tests.core.telegram_client.helpers import patch_action_client as _patch_client

if TYPE_CHECKING:
    from telethon.tl.functions.contacts import ResolvePhoneRequest


@pytest.mark.asyncio
async def test_send_dm_returns_message_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "typing_simulation_enabled", False)

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, user_id: int) -> str:
            return f"peer:{user_id}"

        async def send_message(self, peer: object, text: str) -> object:
            assert peer == "peer:555"
            assert text == "hello"
            return MagicMock(id=88)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-4", SendDirectMessage(user_id=555, text="hello"))

    assert result.status == "ok"
    assert result.message_id == 88


@pytest.mark.asyncio
async def test_send_dm_resolves_phone_when_peer_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression guard for the cold-session "input entity" bug: no DM ever sent.

    Also pins the two properties that pick ``resolvePhone`` over a contact
    import: it leaves no saved contact behind, and the phone is normalised
    before it goes out (raw requests skip Telethon's own ``parse_phone``).
    """
    monkeypatch.setattr(settings.warming, "typing_simulation_enabled", False)
    captured: list[ResolvePhoneRequest] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: ResolvePhoneRequest) -> None:
            captured.append(request)

        async def get_input_entity(self, user_id: int) -> str:
            if not captured:
                msg = f"Could not find the input entity for PeerUser(user_id={user_id})"
                raise ValueError(msg)
            return f"peer:{user_id}"

        async def send_message(self, peer: object, _text: str) -> object:
            assert peer == "peer:555"
            return MagicMock(id=99)

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-cold",
        SendDirectMessage(user_id=555, text="hello", peer_phone="+7 999 000-11-22"),
    )

    assert result.status == "ok"
    assert result.message_id == 99
    assert [type(r).__name__ for r in captured] == ["ResolvePhoneRequest"]
    assert captured[0].phone == "79990001122"


@pytest.mark.asyncio
async def test_send_dm_without_a_phone_is_unresolvable_not_a_bare_value_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No phone and no cached entity is just as permanent as a privacy block.

    A bare ``ValueError`` here would miss the caller's skip branch and park the
    sender in the terminal ``error`` state.
    """
    monkeypatch.setattr(settings.warming, "typing_simulation_enabled", False)

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, user_id: int) -> str:
            msg = f"Could not find the input entity for PeerUser(user_id={user_id})"
            raise ValueError(msg)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-nophone", SendDirectMessage(user_id=555, text="hello"))

    assert result.status == "failed"
    assert result.error_type == "DmPeerUnresolvedError"


@pytest.mark.asyncio
async def test_send_dm_unresolvable_peer_gets_its_own_error_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A phone-privacy block must not read like the pre-fix resolution bug."""
    monkeypatch.setattr(settings.warming, "typing_simulation_enabled", False)

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> None:
            # What Telegram answers for a number hidden by "who can find me by
            # phone number", and for one no account uses — deliberately alike.
            raise errors.PhoneNotOccupiedError(request=None)

        async def get_input_entity(self, user_id: int) -> str:
            msg = f"Could not find the input entity for PeerUser(user_id={user_id})"
            raise ValueError(msg)

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-hidden",
        SendDirectMessage(user_id=555, text="hello", peer_phone="79990001122"),
    )

    assert result.status == "failed"
    assert result.error_type == "DmPeerUnresolvedError"


@pytest.mark.asyncio
async def test_send_dm_lookup_that_reveals_nothing_is_unresolvable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lookup can answer without teaching the session anything usable.

    Distinct from the privacy refusal above: here ``resolvePhone`` returns, but
    the peer still is not resolvable, so the re-resolve miss is what has to be
    classified — not the RPC.
    """
    monkeypatch.setattr(settings.warming, "typing_simulation_enabled", False)

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> None:
            return None  # answered, but populated nothing

        async def get_input_entity(self, user_id: int) -> str:
            msg = f"Could not find the input entity for PeerUser(user_id={user_id})"
            raise ValueError(msg)

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-empty",
        SendDirectMessage(user_id=555, text="hello", peer_phone="79990001122"),
    )

    assert result.status == "failed"
    assert result.error_type == "DmPeerUnresolvedError"


@pytest.mark.asyncio
async def test_send_dm_malformed_phone_is_unresolvable_not_a_repeating_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A digits-but-too-short phone reaches Telegram, so its refusal is permanent.

    ``parse_phone`` strips only "+()- " and checks ``isdigit``, so a truncated
    number passes the pre-flight and comes back PHONE_NUMBER_INVALID. Left
    uncaught it is a plain failure, which re-arms the same message every cycle
    and re-parks the account after each operator restart.
    """
    monkeypatch.setattr(settings.warming, "typing_simulation_enabled", False)

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> None:
            raise errors.PhoneNumberInvalidError(request=None)

        async def get_input_entity(self, user_id: int) -> str:
            msg = f"Could not find the input entity for PeerUser(user_id={user_id})"
            raise ValueError(msg)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-short", SendDirectMessage(user_id=555, text="hi", peer_phone="12"))

    assert result.status == "failed"
    assert result.error_type == "DmPeerUnresolvedError"


@pytest.mark.asyncio
async def test_send_dm_transient_lookup_failure_is_not_called_unresolvable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wobbling datacentre must not be written off as a hidden partner.

    Telethon turns exhausted retries into a bare ``ValueError``. Classifying
    that as ``DmPeerUnresolvedError`` makes the caller skip the pair and consume
    the live message it was answering — permanently, for a fault that clears.
    """
    monkeypatch.setattr(settings.warming, "typing_simulation_enabled", False)

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> None:
            # Verbatim shape of telethon.client.users.UserMethods._call giving
            # up, which it does by default (raise_last_call_error is False).
            msg = "Request was unsuccessful 5 time(s)"
            raise ValueError(msg)

        async def get_input_entity(self, user_id: int) -> str:
            msg = f"Could not find the input entity for PeerUser(user_id={user_id})"
            raise ValueError(msg)

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-wobble",
        SendDirectMessage(user_id=555, text="hello", peer_phone="79990001122"),
    )

    assert result.status == "failed"
    assert result.error_type == "ValueError"


@pytest.mark.asyncio
async def test_send_dm_unparseable_phone_never_reaches_telegram(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A phone with no digits in it is a data bug, not a lookup worth spending."""
    monkeypatch.setattr(settings.warming, "typing_simulation_enabled", False)
    calls: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> None:
            calls.append(request)

        async def get_input_entity(self, user_id: int) -> str:
            msg = f"Could not find the input entity for PeerUser(user_id={user_id})"
            raise ValueError(msg)

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-garbage",
        SendDirectMessage(user_id=555, text="hello", peer_phone="not-a-phone"),
    )

    assert result.status == "failed"
    assert result.error_type == "DmPeerUnresolvedError"
    assert calls == []
