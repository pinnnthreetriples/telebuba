"""Minting a fresh web authorization in-process: the QR handshake, 2FA, and cleanup."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from telethon.errors import SessionPasswordNeededError
from telethon.tl.functions.auth import AcceptLoginTokenRequest

from core.telegram_client import (
    MintedWebAuth,
    TwoFactorRequiredError,
    WebLoginError,
    mint_web_authorization,
)
from schemas.proxy import ProxySettings

if TYPE_CHECKING:
    from collections.abc import Sequence

_MODULE = "core.telegram_client._web_login"
_PROXY = ProxySettings(proxy_type="https", host="1.2.3.4", port=8080, username="u", password="p")
_TOKEN = b"login-token-bytes"
_AUTH_KEY = bytes(range(256))


class _FakeQr:
    """A scripted QR login: exposes ``token`` and a ``wait`` with a chosen outcome."""

    def __init__(self, wait_error: Exception | None = None) -> None:
        self.token = _TOKEN
        self.wait_error = wait_error
        self.wait_calls: list[float | None] = []

    async def wait(self, timeout: float | None = None) -> None:  # noqa: ASYNC109 - mirrors telethon's QRLogin.wait signature
        self.wait_calls.append(timeout)
        if self.wait_error is not None:
            raise self.wait_error


class _FakeSession:
    def __init__(self) -> None:
        self.dc_id = 4
        self.auth_key = type("_Key", (), {"key": _AUTH_KEY})()


class _FakeState:
    salt = 0x0102030405060708


class _FakeSender:
    _state = _FakeState()


class _FakeNewClient:
    """The throwaway "new device" client, with every seam the minter touches."""

    def __init__(self, qr: _FakeQr) -> None:
        self._qr = qr
        self.session = _FakeSession()
        self._sender = _FakeSender()
        self.connected = False
        self.disconnected = False
        self.sign_in_passwords: list[str] = []

    async def connect(self) -> None:
        self.connected = True

    async def qr_login(self) -> _FakeQr:
        return self._qr

    async def get_me(self) -> object:
        return type("_Me", (), {"id": 777})()

    async def sign_in(self, *, password: str) -> None:
        self.sign_in_passwords.append(password)

    async def disconnect(self) -> None:
        self.disconnected = True


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    proxy: ProxySettings | None = _PROXY,
    new_client: _FakeNewClient | None = None,
    twofa_password: str | None = None,
) -> tuple[_FakeNewClient | None, AsyncMock]:
    """Wire the module's four seams; return the fake new client and the confirmer mock."""
    confirmer = AsyncMock(name="confirmer")

    async def fake_proxy(_account_id: str) -> ProxySettings | None:
        return proxy

    async def fake_get_client(_account_id: str) -> AsyncMock:
        return confirmer

    async def fake_twofa(_account_id: str) -> str | None:
        return twofa_password

    monkeypatch.setattr(f"{_MODULE}.fetch_account_proxy_settings", fake_proxy)
    monkeypatch.setattr(f"{_MODULE}.get_client", fake_get_client)
    monkeypatch.setattr(f"{_MODULE}.fetch_account_twofa_password", fake_twofa)
    monkeypatch.setattr(f"{_MODULE}.TelegramClient", lambda *_a, **_k: new_client)
    return new_client, confirmer


def _accept_tokens(confirmer: AsyncMock) -> Sequence[bytes]:
    """The token bytes the confirmer received via ``AcceptLoginTokenRequest``."""
    tokens = []
    for call in confirmer.await_args_list:
        (request,) = call.args
        assert isinstance(request, AcceptLoginTokenRequest)
        tokens.append(request.token)
    return tokens


@pytest.mark.asyncio
async def test_happy_path_confirms_token_and_returns_minted_material(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qr = _FakeQr()
    client = _FakeNewClient(qr)
    _, confirmer = _patch(monkeypatch, new_client=client)

    result = await mint_web_authorization("acc-1")

    assert client.connected
    # The confirmer accepted exactly the token the fresh client's QR exposed.
    assert _accept_tokens(confirmer) == [_TOKEN]
    assert qr.wait_calls == [30.0]
    assert result == MintedWebAuth(
        dc_id=4,
        auth_key=_AUTH_KEY,
        server_salt=(0x0102030405060708).to_bytes(8, "little"),
        user_id=777,
    )
    assert client.disconnected


@pytest.mark.asyncio
async def test_new_client_is_disconnected_even_when_login_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qr = _FakeQr(wait_error=RuntimeError("token expired"))
    client = _FakeNewClient(qr)
    _patch(monkeypatch, new_client=client)

    with pytest.raises(WebLoginError):
        await mint_web_authorization("acc-1")

    assert client.disconnected


@pytest.mark.asyncio
async def test_two_factor_with_stored_password_signs_in_and_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qr = _FakeQr(wait_error=SessionPasswordNeededError(request=None))
    client = _FakeNewClient(qr)
    _patch(monkeypatch, new_client=client, twofa_password="hunter2")

    result = await mint_web_authorization("acc-1")

    assert client.sign_in_passwords == ["hunter2"]
    assert result.user_id == 777
    assert client.disconnected


@pytest.mark.asyncio
async def test_two_factor_without_stored_password_raises_two_factor_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qr = _FakeQr(wait_error=SessionPasswordNeededError(request=None))
    client = _FakeNewClient(qr)
    _patch(monkeypatch, new_client=client, twofa_password=None)

    with pytest.raises(TwoFactorRequiredError):
        await mint_web_authorization("acc-1")

    assert client.sign_in_passwords == []
    assert client.disconnected


@pytest.mark.asyncio
async def test_missing_proxy_raises_web_login_error_before_building_a_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, confirmer = _patch(monkeypatch, proxy=None, new_client=None)

    with pytest.raises(WebLoginError):
        await mint_web_authorization("acc-1")

    # No confirmer borrow, no client build — we bailed on the proxy check.
    confirmer.assert_not_awaited()


@pytest.mark.asyncio
async def test_server_salt_is_none_when_connection_has_no_salt_yet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qr = _FakeQr()
    client = _FakeNewClient(qr)
    client._sender._state.salt = 0
    _patch(monkeypatch, new_client=client)

    result = await mint_web_authorization("acc-1")

    assert result.server_salt is None
