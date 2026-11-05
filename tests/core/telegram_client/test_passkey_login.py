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
from core.telegram_client import _passkey_login, _twofa_candidates
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

    def __init__(  # noqa: PLR0913 - one switch per scripted behaviour
        self,
        *,
        authorized: bool = False,
        finish: Exception | None = None,
        me_id: int = _USER_ID,
        connect_error: Exception | None = None,
        session: object = "default",
        finish_once: Exception | None = None,
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
        self.finish_once = finish_once
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
        if isinstance(request, FinishPasskeyLoginRequest) and self.finish_once is not None:
            error, self.finish_once = self.finish_once, None
            raise error
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

    async def _candidates(account_id: str) -> list[str]:
        assert account_id == "acc-1"
        passwords = state["password"]
        if passwords is None:
            return []
        if isinstance(passwords, str):
            return [passwords]
        return [str(item) for item in passwords]  # ty: ignore[not-iterable]

    async def _confirm(account_id: str, password: str) -> bool:
        state.setdefault("confirmed", []).append((account_id, password))  # ty: ignore[unresolved-attribute]
        return True

    async def _backoff(seconds: float) -> None:
        state.setdefault("slept", []).append(seconds)  # ty: ignore[unresolved-attribute]

    monkeypatch.setattr(_passkey_login, "prepare_telegram_client_profile", _profile)
    monkeypatch.setattr(_passkey_login, "create_telegram_client", lambda _profile: state["client"])
    monkeypatch.setattr(_passkey_login, "removing_client", _removing)
    monkeypatch.setattr(_passkey_login, "sign_with_key", _sign)
    monkeypatch.setattr(_passkey_login, "twofa_password_candidates", _candidates)
    monkeypatch.setattr(_twofa_candidates, "confirm_twofa_password_used", _confirm)
    monkeypatch.setattr(_passkey_login, "_backoff", _backoff)
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
async def test_a_stored_password_telegram_rejects_is_reported_as_invalid(
    wire: dict[str, object],
) -> None:
    client = _Client(finish=errors.SessionPasswordNeededError(request=None))

    async def _rejected(**_: str) -> None:
        raise errors.PasswordHashInvalidError(request=None)

    client.sign_in = _rejected  # ty: ignore[invalid-assignment]
    wire["client"] = client

    result = await login_with_passkey(_passkey(), None)

    assert result.status == "unauthorized"
    assert result.error_message == "passkey_twofa_invalid"


@pytest.mark.asyncio
async def test_a_passkey_telegram_refuses_is_a_refusal_not_a_temporary_fault(
    wire: dict[str, object],
) -> None:
    wire["client"] = _Client(finish=errors.RPCError(request=None, message="PASSKEY_INVALID"))

    result = await login_with_passkey(_passkey(), None)

    assert result.status == "unauthorized"
    assert result.error_message == "passkey_telegram_refused"
    assert result.is_temporary is False


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
async def test_an_authorized_session_of_another_user_is_a_mismatch_and_left_alone(
    wire: dict[str, object],
) -> None:
    client = _Client(authorized=True, me_id=999)
    wire["client"] = client

    result = await login_with_passkey(_passkey(), None)

    assert result.status == "unauthorized"
    assert result.error_message == "passkey_user_mismatch"
    assert client.requests == []
    assert "log_out" not in client.calls


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
    # The non-secret detail rides on the verdict for the journal.
    assert result.error_type == "ConnectError"
    # Transient: three attempts with a bounded backoff between them.
    assert len(wire["signed"]) == 3  # ty: ignore[invalid-argument-type]
    assert wire["slept"] == list(_passkey_login._RETRY_DELAYS)


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


class _FlakyVault:
    """A ``sign_with_key`` double failing ``failures`` times, then signing."""

    def __init__(self, error: Exception, failures: int) -> None:
        self.error = error
        self.failures = failures
        self.calls = 0

    async def __call__(self, name: str, data: bytes) -> bytes:  # noqa: ARG002
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return _SIGNATURE


@pytest.mark.asyncio
async def test_a_transient_vault_failure_is_retried_and_then_succeeds(
    wire: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = _FlakyVault(PasskeyVaultError("passkey_vault_unreachable", "ConnectError"), 2)
    monkeypatch.setattr(_passkey_login, "sign_with_key", vault)

    result = await login_with_passkey(_passkey(), None)

    assert result.status == "alive"
    assert vault.calls == 3
    assert wire["slept"] == list(_passkey_login._RETRY_DELAYS)


@pytest.mark.parametrize(
    ("code", "detail"),
    [
        ("passkey_vault_sealed", "http_503"),
        ("passkey_vault_forbidden", "http_403"),
        ("passkey_vault_key_missing", "http_404"),
        ("passkey_vault_rejected", "http_400"),
    ],
)
@pytest.mark.asyncio
async def test_a_definitive_vault_refusal_is_never_retried(
    wire: dict[str, object], monkeypatch: pytest.MonkeyPatch, code: str, detail: str
) -> None:
    vault = _FlakyVault(PasskeyVaultError(code, detail), 99)  # ty: ignore[invalid-argument-type]
    monkeypatch.setattr(_passkey_login, "sign_with_key", vault)

    result = await login_with_passkey(_passkey(), None)

    assert (result.error_message, result.error_type) == (code, detail)
    assert vault.calls == 1
    assert "slept" not in wire


@pytest.mark.asyncio
async def test_a_network_failure_during_the_finish_is_retried(wire: dict[str, object]) -> None:
    client = _Client(finish_once=TimeoutError())
    wire["client"] = client

    result = await login_with_passkey(_passkey(), None)

    assert result.status == "alive"
    assert len([r for r in client.requests if isinstance(r, FinishPasskeyLoginRequest)]) == 2
    assert wire["slept"] == [_passkey_login._RETRY_DELAYS[0]]


@pytest.mark.asyncio
async def test_a_telegram_refusal_is_never_retried(wire: dict[str, object]) -> None:
    client = _Client(finish=errors.RPCError(request=None, message="PASSKEY_INVALID"))
    wire["client"] = client

    result = await login_with_passkey(_passkey(), None)

    assert result.error_message == "passkey_telegram_refused"
    assert len([r for r in client.requests if isinstance(r, FinishPasskeyLoginRequest)]) == 1
    assert "slept" not in wire


@pytest.mark.asyncio
async def test_the_pending_candidate_signs_in_when_the_current_password_is_rejected(
    wire: dict[str, object],
) -> None:
    client = _Client(finish=errors.SessionPasswordNeededError(request=None))

    async def _sign_in(*, password: str) -> None:
        client.calls.append(f"sign_in:{password}")
        if password == "old-pass":
            raise errors.PasswordHashInvalidError(request=None)

    client.sign_in = _sign_in  # ty: ignore[invalid-assignment]
    wire["client"] = client
    wire["password"] = ["old-pass", "new-pass"]

    result = await login_with_passkey(_passkey(), None)

    assert result.status == "alive"
    assert [c for c in client.calls if c.startswith("sign_in")] == [
        "sign_in:old-pass",
        "sign_in:new-pass",
    ]
    assert wire["confirmed"] == [("acc-1", "new-pass")]


@pytest.mark.asyncio
async def test_every_candidate_rejected_is_reported_as_invalid(wire: dict[str, object]) -> None:
    client = _Client(finish=errors.SessionPasswordNeededError(request=None))

    async def _rejected(*, password: str) -> None:  # noqa: ARG001
        raise errors.PasswordHashInvalidError(request=None)

    client.sign_in = _rejected  # ty: ignore[invalid-assignment]
    wire["client"] = client
    wire["password"] = ["a", "b"]

    result = await login_with_passkey(_passkey(), None)

    assert result.error_message == "passkey_twofa_invalid"
    assert "confirmed" not in wire
