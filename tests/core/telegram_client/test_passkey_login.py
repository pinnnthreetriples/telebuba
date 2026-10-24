"""Passkey sign-in on the account's own session: DC hand-off, 2FA, wrong user, failures."""

from __future__ import annotations

import contextlib
import json
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from telethon import errors
from telethon.tl.functions.auth import FinishPasskeyLoginRequest, InitPasskeyLoginRequest
from telethon.tl.types import InputPasskeyCredentialPublicKey, InputPasskeyResponseLogin

from core.passkey_vault import PasskeyVaultError
from core.telegram_client import _passkey_login
from core.telegram_client._passkey_login import login_with_passkey
from schemas.passkey import StoredPasskey

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_USER_ID = 257221571
_SIGNATURE = b"\x30\x44der"


def _passkey(dc_id: int = 2) -> StoredPasskey:
    return StoredPasskey(
        account_id="acc-1",
        credential_id="Y3JlZA",
        user_handle=f"{dc_id}:{_USER_ID}",
        vault_key_name="tb-acc-1",
        created_at="2026-10-24T12:00:00+00:00",
    )


class _Client:
    """A Telethon client double: one session on DC 2, scripted answers."""

    def __init__(
        self,
        *,
        authorized: bool = False,
        finish: Exception | None = None,
        me_id: int = _USER_ID,
        connect_error: Exception | None = None,
        session: object = "default",
    ) -> None:
        self.session = (
            SimpleNamespace(dc_id=2, auth_key=SimpleNamespace(key_id=(1 << 64) - 5))
            if session == "default"
            else session
        )
        self.api_id = 6
        self.api_hash = "hash"
        self.authorized = authorized
        self.finish = finish
        self.me_id = me_id
        self.connect_error = connect_error
        self.requests: list[object] = []
        self.calls: list[str] = []

    async def connect(self) -> None:
        if self.connect_error is not None:
            raise self.connect_error

    async def is_user_authorized(self) -> bool:
        return self.authorized

    async def __call__(self, request: object) -> object:
        self.requests.append(request)
        if isinstance(request, InitPasskeyLoginRequest):
            payload = {"publicKey": {"challenge": "bG9naW4"}}
            return SimpleNamespace(options=SimpleNamespace(data=json.dumps(payload)))
        if self.finish is not None:
            raise self.finish
        return SimpleNamespace(user=SimpleNamespace(id=self.me_id))

    async def _switch_dc(self, dc_id: int) -> None:
        self.calls.append(f"switch:{dc_id}")

    async def sign_in(self, *, password: str) -> None:
        self.calls.append(f"sign_in:{password}")

    async def get_me(self) -> object:
        return SimpleNamespace(id=self.me_id, phone="5215550001111", username="tanechka_mu")

    async def log_out(self) -> None:
        self.calls.append("log_out")

    async def disconnect(self) -> None:
        self.calls.append("disconnect")


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Patch the module's seams; the returned dict carries the client and the 2FA password."""
    state: dict[str, object] = {"client": _Client(), "password": "cloud-pass", "signed": []}

    async def _profile(request: object) -> object:
        state["session_name"] = getattr(request, "session_name", None)
        return SimpleNamespace(account_id="acc-1", session_path="sessions/acc-1", device_type=None)

    @contextlib.asynccontextmanager
    async def _removing(account_id: str, device_type: object) -> AsyncIterator[None]:
        state["removing"] = (account_id, device_type)
        yield

    async def _sign(name: str, data: bytes) -> bytes:
        state["signed"].append((name, data))  # ty: ignore[unresolved-attribute]
        error = state.get("sign_error")
        if isinstance(error, Exception):
            raise error
        return _SIGNATURE

    async def _password(account_id: str) -> str | None:
        assert account_id == "acc-1"
        return state["password"]  # ty: ignore[invalid-return-type]

    monkeypatch.setattr(_passkey_login, "prepare_telegram_client_profile", _profile)
    monkeypatch.setattr(_passkey_login, "create_telegram_client", lambda _profile: state["client"])
    monkeypatch.setattr(_passkey_login, "removing_client", _removing)
    monkeypatch.setattr(_passkey_login, "sign_with_key", _sign)
    monkeypatch.setattr(_passkey_login, "fetch_account_twofa_password", _password)
    return state


def _finish(client: _Client) -> FinishPasskeyLoginRequest:
    (request,) = [r for r in client.requests if isinstance(r, FinishPasskeyLoginRequest)]
    return request


def _login_response(request: FinishPasskeyLoginRequest) -> InputPasskeyResponseLogin:
    assert isinstance(request.credential, InputPasskeyCredentialPublicKey)
    response = request.credential.response
    assert isinstance(response, InputPasskeyResponseLogin)
    return response


@pytest.mark.asyncio
async def test_a_login_on_the_home_dc_signs_the_challenge(wire: dict[str, object]) -> None:
    client: _Client = wire["client"]  # ty: ignore[invalid-assignment]

    result = await login_with_passkey(_passkey(dc_id=2), "acc-1-session")

    assert result.status == "alive"
    assert result.user_id == _USER_ID
    assert wire["session_name"] == "acc-1-session"
    assert wire["removing"] == ("acc-1", None)
    request = _finish(client)
    assert request.from_dc_id is None
    response = _login_response(request)
    assert response.signature == _SIGNATURE
    assert response.user_handle == f"2:{_USER_ID}"
    ((key_name, signed),) = wire["signed"]  # ty: ignore[not-iterable]
    assert key_name == "tb-acc-1"
    assert signed[:32] == response.authenticator_data[:32]
    assert client.calls == ["disconnect"]


@pytest.mark.asyncio
async def test_a_login_on_another_dc_switches_and_names_where_the_challenge_came_from(
    wire: dict[str, object],
) -> None:
    client: _Client = wire["client"]  # ty: ignore[invalid-assignment]

    result = await login_with_passkey(_passkey(dc_id=1), None)

    assert result.status == "alive"
    assert client.calls[0] == "switch:1"
    request = _finish(client)
    assert request.from_dc_id == 2
    assert request.from_auth_key_id == -5  # the unsigned key id as a signed TL long


@pytest.mark.asyncio
async def test_the_stored_cloud_password_completes_a_2fa_account(wire: dict[str, object]) -> None:
    client = _Client(finish=errors.SessionPasswordNeededError(request=None))
    wire["client"] = client

    result = await login_with_passkey(_passkey(), None)

    assert result.status == "alive"
    assert "sign_in:cloud-pass" in client.calls


@pytest.mark.asyncio
async def test_a_2fa_account_without_a_stored_password_is_refused(wire: dict[str, object]) -> None:
    wire["client"] = _Client(finish=errors.SessionPasswordNeededError(request=None))
    wire["password"] = None

    result = await login_with_passkey(_passkey(), None)

    assert result.status == "unauthorized"
    assert result.error_message == "passkey_twofa_missing"


@pytest.mark.asyncio
async def test_an_authorized_session_is_reported_alive_without_a_login(
    wire: dict[str, object],
) -> None:
    client = _Client(authorized=True)
    wire["client"] = client

    result = await login_with_passkey(_passkey(), None)

    assert result.status == "alive"
    assert client.requests == []
    assert wire["signed"] == []


@pytest.mark.asyncio
async def test_a_login_that_lands_on_another_user_is_logged_out(wire: dict[str, object]) -> None:
    client = _Client(me_id=999)
    wire["client"] = client

    result = await login_with_passkey(_passkey(), None)

    assert result.status == "unauthorized"
    assert result.error_message == "passkey_user_mismatch"
    assert client.calls == ["log_out", "disconnect"]


@pytest.mark.asyncio
async def test_a_failed_logout_after_a_wrong_user_still_reports_the_mismatch(
    wire: dict[str, object],
) -> None:
    client = _Client(me_id=999)

    async def _broken() -> None:
        raise ConnectionError

    client.log_out = _broken  # ty: ignore[invalid-assignment]
    wire["client"] = client

    result = await login_with_passkey(_passkey(), None)

    assert result.error_message == "passkey_user_mismatch"


@pytest.mark.asyncio
async def test_a_vault_refusal_is_reported_by_its_code(wire: dict[str, object]) -> None:
    wire["sign_error"] = PasskeyVaultError("passkey_vault_unreachable", "ConnectError")

    result = await login_with_passkey(_passkey(), None)

    assert result.status == "unauthorized"
    assert result.error_message == "passkey_vault_unreachable"


@pytest.mark.asyncio
async def test_a_session_without_an_auth_key_is_unavailable(wire: dict[str, object]) -> None:
    wire["client"] = _Client(session=SimpleNamespace(dc_id=2, auth_key=None))

    result = await login_with_passkey(_passkey(), None)

    assert result.status == "unauthorized"
    assert result.error_message == "unavailable"


@pytest.mark.asyncio
async def test_a_flood_wait_is_temporary_with_its_seconds(wire: dict[str, object]) -> None:
    wire["client"] = _Client(finish=errors.FloodWaitError(request=None, capture=30))

    result = await login_with_passkey(_passkey(), None)

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 30
    assert result.is_temporary is True


@pytest.mark.asyncio
async def test_any_other_failure_keeps_only_the_class_name(wire: dict[str, object]) -> None:
    wire["client"] = _Client(connect_error=ConnectionError("socks5://user:pass@host:1080"))

    result = await login_with_passkey(_passkey(), None)

    assert result.status == "unknown_error"
    assert result.error_message == "ConnectionError"
    assert "user:pass" not in result.model_dump_json()
