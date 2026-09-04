"""Accepting a browser's QR login token with the account's own pooled client."""

from __future__ import annotations

import pytest
from telethon.tl.functions.auth import AcceptLoginTokenRequest

from core.telegram_client import accept_web_login_token

_MODULE = "core.telegram_client._web_login"
_TOKEN = b"login-token-bytes"


@pytest.mark.asyncio
async def test_accept_web_login_token_calls_accept_with_the_given_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []

    async def fake_get_client(account_id: str) -> object:
        assert account_id == "acc-1"

        async def confirmer(request: object) -> None:
            captured.append(request)

        return confirmer

    monkeypatch.setattr(f"{_MODULE}.get_client", fake_get_client)

    await accept_web_login_token("acc-1", _TOKEN)

    (request,) = captured
    assert isinstance(request, AcceptLoginTokenRequest)
    assert request.token == _TOKEN


@pytest.mark.asyncio
async def test_accept_web_login_token_lets_accept_errors_propagate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _RotatedError(Exception):
        """Stands in for Telethon's AUTH_TOKEN_* rotation errors."""

    async def fake_get_client(_account_id: str) -> object:
        async def confirmer(_request: object) -> None:
            raise _RotatedError

        return confirmer

    monkeypatch.setattr(f"{_MODULE}.get_client", fake_get_client)

    with pytest.raises(_RotatedError):
        await accept_web_login_token("acc-1", _TOKEN)
