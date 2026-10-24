"""Passkey registration and removal on the pooled client, every failure as a bounded code."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from telethon import errors
from telethon.tl.functions.account import (
    DeletePasskeyRequest,
    GetPasskeysRequest,
    InitPasskeyRegistrationRequest,
    RegisterPasskeyRequest,
)
from telethon.tl.types import InputPasskeyCredentialPublicKey, InputPasskeyResponseRegister

from core.telegram_client import _passkey
from core.telegram_client._passkey import (
    PasskeyGatewayError,
    delete_passkey,
    public_key_options,
    register_passkey,
)
from core.telegram_client._pool import TelegramClientPoolError
from core.webauthn_passkey import b64url, new_credential_id

_HANDLE = "1:257221571"
_PEM = (
    ec.generate_private_key(ec.SECP256R1())
    .public_key()
    .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    .decode()
)


def _options(user_id: str | None = None, challenge: object = "Y2hhbGxlbmdl") -> SimpleNamespace:
    public = {
        "challenge": challenge,
        "rp": {"id": "telegram.org"},
        "user": {"id": b64url(_HANDLE.encode()) if user_id is None else user_id},
    }
    return SimpleNamespace(options=SimpleNamespace(data=json.dumps({"publicKey": public})))


class _Client:
    """Records every TL request; answers each one from ``replies`` by request type."""

    def __init__(self, replies: dict[type, object]) -> None:
        self.replies = replies
        self.requests: list[object] = []

    async def __call__(self, request: object) -> object:
        self.requests.append(request)
        reply = self.replies.get(type(request))
        if isinstance(reply, Exception):
            raise reply
        return reply


def _borrow(monkeypatch: pytest.MonkeyPatch, client: object) -> None:
    async def _get_client(account_id: str) -> object:
        assert account_id == "acc-1"
        if isinstance(client, Exception):
            raise client
        return client

    monkeypatch.setattr(_passkey, "get_client", _get_client)


@pytest.mark.asyncio
async def test_registration_sends_the_credential_and_returns_the_user_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client({InitPasskeyRegistrationRequest: _options(), RegisterPasskeyRequest: object()})
    _borrow(monkeypatch, client)
    credential_id = new_credential_id()

    handle = await register_passkey("acc-1", public_key_pem=_PEM, credential_id=credential_id)

    assert handle == _HANDLE
    sent = client.requests[-1]
    assert isinstance(sent, RegisterPasskeyRequest)
    credential = sent.credential
    assert isinstance(credential, InputPasskeyCredentialPublicKey)
    assert credential.id == credential_id
    assert credential.raw_id == credential_id
    response = credential.response
    assert isinstance(response, InputPasskeyResponseRegister)
    client_data = json.loads(response.client_data.data)
    assert client_data["type"] == "webauthn.create"
    assert client_data["challenge"] == "Y2hhbGxlbmdl"
    assert response.attestation_data.startswith(b"\xa3\x63fmt\x64none")


@pytest.mark.parametrize(
    ("raised", "code", "seconds"),
    [
        (errors.FloodWaitError(request=None, capture=42), "flood_wait", 42),
        (
            errors.RPCError(request=None, message="PASSKEY_INVALID"),
            "passkey_telegram_refused",
            None,
        ),
        (ConnectionError("socks5://user:pass@host"), "unavailable", None),
    ],
)
@pytest.mark.asyncio
async def test_telegram_failures_become_bounded_codes(
    monkeypatch: pytest.MonkeyPatch,
    raised: Exception,
    code: str,
    seconds: int | None,
) -> None:
    _borrow(monkeypatch, _Client({InitPasskeyRegistrationRequest: raised}))
    with pytest.raises(PasskeyGatewayError) as caught:
        await register_passkey("acc-1", public_key_pem=_PEM, credential_id=new_credential_id())
    assert caught.value.code == code
    assert caught.value.retry_after_seconds == seconds
    assert "user:pass" not in str(caught.value)


@pytest.mark.asyncio
async def test_an_unusable_pool_client_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _borrow(monkeypatch, TelegramClientPoolError("acc-1", OSError("down")))
    with pytest.raises(PasskeyGatewayError) as caught:
        await register_passkey("acc-1", public_key_pem=_PEM, credential_id=new_credential_id())
    assert caught.value.code == "unavailable"
    assert caught.value.error_type == "TelegramClientPoolError"


@pytest.mark.parametrize(
    "user_id",
    ["", b64url(b"not-a-handle"), b64url(b"\xff\xfe"), "!!!"],
)
@pytest.mark.asyncio
async def test_options_without_a_usable_user_handle_are_refused(
    monkeypatch: pytest.MonkeyPatch, user_id: str
) -> None:
    client = _Client({InitPasskeyRegistrationRequest: _options(user_id=user_id)})
    _borrow(monkeypatch, client)
    with pytest.raises(PasskeyGatewayError) as caught:
        await register_passkey("acc-1", public_key_pem=_PEM, credential_id=new_credential_id())
    assert caught.value.code == "passkey_options_invalid"
    assert not any(isinstance(r, RegisterPasskeyRequest) for r in client.requests)


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        "[]",
        json.dumps({"publicKey": None}),
        json.dumps({"publicKey": {"challenge": 7}}),
    ],
)
def test_options_without_a_challenge_are_refused(payload: str) -> None:
    with pytest.raises(PasskeyGatewayError) as caught:
        public_key_options(payload)
    assert caught.value.code == "passkey_options_invalid"


@pytest.mark.asyncio
async def test_removal_deletes_only_the_matching_passkey(monkeypatch: pytest.MonkeyPatch) -> None:
    listed = SimpleNamespace(passkeys=[SimpleNamespace(id="other"), SimpleNamespace(id="mine")])
    client = _Client({GetPasskeysRequest: listed, DeletePasskeyRequest: True})
    _borrow(monkeypatch, client)

    assert await delete_passkey("acc-1", "mine") is True

    deleted = [r.id for r in client.requests if isinstance(r, DeletePasskeyRequest)]
    assert deleted == ["mine"]


@pytest.mark.asyncio
async def test_removing_a_passkey_telegram_no_longer_has_reports_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client({GetPasskeysRequest: SimpleNamespace(passkeys=[])})
    _borrow(monkeypatch, client)
    assert await delete_passkey("acc-1", "mine") is False
    assert not any(isinstance(r, DeletePasskeyRequest) for r in client.requests)
