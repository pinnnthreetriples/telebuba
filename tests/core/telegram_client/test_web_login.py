"""Accepting a browser's QR login token with the account's own pooled client."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from telethon.tl.functions.account import ChangeAuthorizationSettingsRequest
from telethon.tl.functions.auth import AcceptLoginTokenRequest

from core.telegram_client import accept_web_login_token

_MODULE = "core.telegram_client._web_login"
_TOKEN = b"login-token-bytes"


@pytest.mark.asyncio
async def test_accept_web_login_token_calls_accept_with_the_given_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []

    async def fake_get_client(account_id: str, _device_type: str | None = None) -> object:
        assert account_id == "acc-1"

        async def confirmer(request: object) -> object:
            captured.append(request)
            return SimpleNamespace(hash=1234567890123)

        return confirmer

    monkeypatch.setattr(f"{_MODULE}.get_client", fake_get_client)

    assert await accept_web_login_token("acc-1", _TOKEN) == "1234567890123"

    (request,) = captured
    assert isinstance(request, AcceptLoginTokenRequest)
    assert request.token == _TOKEN


@pytest.mark.asyncio
async def test_accept_web_login_token_lets_accept_errors_propagate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _RotatedError(Exception):
        """Stands in for Telethon's AUTH_TOKEN_* rotation errors."""

    async def fake_get_client(_account_id: str, _device_type: str | None = None) -> object:
        async def confirmer(_request: object) -> None:
            raise _RotatedError

        return confirmer

    monkeypatch.setattr(f"{_MODULE}.get_client", fake_get_client)

    with pytest.raises(_RotatedError):
        await accept_web_login_token("acc-1", _TOKEN)


def _patch_client(monkeypatch: pytest.MonkeyPatch, answer: object, *, confirm_error: bool = False):
    sent: list[object] = []

    async def fake_get_client(_account_id: str, _device_type: str | None = None) -> object:
        async def confirmer(request: object) -> object:
            sent.append(request)
            if isinstance(request, ChangeAuthorizationSettingsRequest):
                if confirm_error:
                    raise ConnectionError
                return True
            return answer

        return confirmer

    monkeypatch.setattr(f"{_MODULE}.get_client", fake_get_client)
    return sent


@pytest.mark.asyncio
async def test_an_unconfirmed_authorization_is_confirmed_once_with_the_same_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = _patch_client(monkeypatch, SimpleNamespace(hash=777, unconfirmed=True))

    assert await accept_web_login_token("acc-1", _TOKEN) == "777"

    confirms = [r for r in sent if isinstance(r, ChangeAuthorizationSettingsRequest)]
    assert len(confirms) == 1
    assert confirms[0].hash == 777
    assert confirms[0].confirmed is True


@pytest.mark.asyncio
async def test_a_failed_confirmation_does_not_fail_the_acceptance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = _patch_client(
        monkeypatch, SimpleNamespace(hash=777, unconfirmed=True), confirm_error=True
    )

    assert await accept_web_login_token("acc-1", _TOKEN) == "777"
    assert len(sent) == 2  # accepted once, confirmation tried once, nothing retried


@pytest.mark.asyncio
async def test_no_confirmation_when_the_authorization_is_not_flagged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = _patch_client(monkeypatch, SimpleNamespace(hash=777, unconfirmed=False))

    assert await accept_web_login_token("acc-1", _TOKEN) == "777"
    assert len(sent) == 1
