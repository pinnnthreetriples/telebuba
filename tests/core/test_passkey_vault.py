"""The key-vault gateway (``core.passkey_vault``): Transit calls, bounded errors, no token leak."""

from __future__ import annotations

import base64
import json

import httpx
import pytest
import respx

from core.config import settings
from core.passkey_vault import (
    PasskeyVaultError,
    create_signing_key,
    delete_signing_key,
    passkey_vault_configured,
    sign_with_key,
    vault_key_name,
)

_TOKEN = "s.app-token-value"
_BASE = "http://vault.test:8200/v1/transit"
_PEM_V1 = "-----BEGIN PUBLIC KEY-----\nv1\n-----END PUBLIC KEY-----\n"
_PEM_V2 = "-----BEGIN PUBLIC KEY-----\nv2\n-----END PUBLIC KEY-----\n"


@pytest.fixture(autouse=True)
def _vault(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.passkey_vault, "addr", "http://vault.test:8200/")
    monkeypatch.setattr(settings.passkey_vault, "token", _TOKEN)
    monkeypatch.setattr(settings.passkey_vault, "mount", "transit")
    monkeypatch.setattr(settings.passkey_vault, "key_prefix", "tb-")


def test_the_key_name_is_the_prefix_and_the_account_id() -> None:
    assert vault_key_name("257221571_telethon") == "tb-257221571_telethon"


def test_the_vault_is_configured_only_with_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    assert passkey_vault_configured() is True
    monkeypatch.setattr(settings.passkey_vault, "token", "")
    assert passkey_vault_configured() is False


@pytest.mark.asyncio
async def test_creating_a_key_asks_for_a_non_exportable_p256_and_returns_the_newest_pem() -> None:
    with respx.mock:
        create = respx.post(f"{_BASE}/keys/tb-acc").mock(return_value=httpx.Response(204))
        respx.get(f"{_BASE}/keys/tb-acc").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {"keys": {"1": {"public_key": _PEM_V1}, "2": {"public_key": _PEM_V2}}}
                },
            ),
        )
        pem = await create_signing_key("tb-acc")
    assert pem == _PEM_V2
    assert json.loads(create.calls.last.request.content) == {
        "type": "ecdsa-p256",
        "exportable": False,
    }
    assert create.calls.last.request.headers["X-Vault-Token"] == _TOKEN


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        ({"data": {"keys": {}}}, "no_key_versions"),
        ({"data": None}, "no_key_versions"),
        ({"data": {"keys": {"1": {"public_key": ""}}}}, "no_public_key"),
        ({"data": {"keys": {"1": "not-a-dict"}}}, "no_public_key"),
    ],
)
@pytest.mark.asyncio
async def test_a_key_read_without_a_public_key_is_rejected(body: object, detail: str) -> None:
    with respx.mock:
        respx.post(f"{_BASE}/keys/tb-acc").mock(return_value=httpx.Response(204))
        respx.get(f"{_BASE}/keys/tb-acc").mock(return_value=httpx.Response(200, json=body))
        with pytest.raises(PasskeyVaultError) as caught:
            await create_signing_key("tb-acc")
    assert caught.value.code == "passkey_vault_rejected"
    assert caught.value.detail == detail


@pytest.mark.asyncio
async def test_signing_returns_the_der_bytes_behind_the_version_prefix() -> None:
    der = b"\x30\x44signature-bytes"
    with respx.mock:
        route = respx.post(f"{_BASE}/sign/tb-acc/sha2-256").mock(
            return_value=httpx.Response(
                200, json={"data": {"signature": "vault:v1:" + base64.b64encode(der).decode()}}
            ),
        )
        signature = await sign_with_key("tb-acc", b"payload")
    assert signature == der
    assert json.loads(route.calls.last.request.content) == {
        "input": base64.b64encode(b"payload").decode(),
        "marshaling_algorithm": "asn1",
    }


@pytest.mark.parametrize("body", [{"data": {"signature": "raw"}}, {"data": None}, {}])
@pytest.mark.asyncio
async def test_a_malformed_signature_is_rejected(body: object) -> None:
    with respx.mock:
        respx.post(f"{_BASE}/sign/tb-acc/sha2-256").mock(
            return_value=httpx.Response(200, json=body)
        )
        with pytest.raises(PasskeyVaultError) as caught:
            await sign_with_key("tb-acc", b"payload")
    assert caught.value.code == "passkey_vault_rejected"
    assert caught.value.detail == "bad_signature_format"


@pytest.mark.asyncio
async def test_deleting_a_key_first_allows_deletion() -> None:
    with respx.mock:
        config = respx.post(f"{_BASE}/keys/tb-acc/config").mock(return_value=httpx.Response(200))
        delete = respx.delete(f"{_BASE}/keys/tb-acc").mock(return_value=httpx.Response(204))
        await delete_signing_key("tb-acc")
    assert json.loads(config.calls.last.request.content) == {"deletion_allowed": True}
    assert delete.called


@pytest.mark.asyncio
async def test_no_token_refuses_without_any_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.passkey_vault, "token", "")
    with respx.mock(assert_all_called=False) as mock:
        with pytest.raises(PasskeyVaultError) as caught:
            await sign_with_key("tb-acc", b"payload")
        assert not mock.calls
    assert caught.value.code == "passkey_vault_disabled"


@pytest.mark.asyncio
async def test_a_refusal_keeps_only_the_status_never_the_token() -> None:
    with respx.mock:
        respx.post(f"{_BASE}/sign/tb-acc/sha2-256").mock(
            return_value=httpx.Response(403, json={"errors": [f"permission denied {_TOKEN}"]})
        )
        with pytest.raises(PasskeyVaultError) as caught:
            await sign_with_key("tb-acc", b"payload")
    assert caught.value.code == "passkey_vault_rejected"
    assert caught.value.detail == "http_403"
    assert _TOKEN not in str(caught.value)


@pytest.mark.asyncio
async def test_an_unreachable_vault_keeps_only_the_class_name() -> None:
    with respx.mock:
        respx.post(f"{_BASE}/sign/tb-acc/sha2-256").mock(side_effect=httpx.ConnectError("boom"))
        with pytest.raises(PasskeyVaultError) as caught:
            await sign_with_key("tb-acc", b"payload")
    assert caught.value.code == "passkey_vault_unreachable"
    assert caught.value.detail == "ConnectError"
