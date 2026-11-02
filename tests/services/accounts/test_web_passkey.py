"""The web window's passkey signer: offered only with a vault and a passkey, per account."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from core.webauthn_passkey import b64url_decode
from schemas.passkey import StoredPasskey
from services.accounts import _web_passkey, web_login

if TYPE_CHECKING:
    from pathlib import Path

_ROW = StoredPasskey(
    account_id="acc-1",
    credential_id="Y3JlZA",
    user_handle="1:257221571",
    vault_key_name="tb-acc-1",
    created_at="2026-10-24T12:00:00+00:00",
)


def _wire(monkeypatch: pytest.MonkeyPatch, *, configured: bool, row: StoredPasskey | None) -> None:
    async def _fetch(_account_id: str) -> StoredPasskey | None:
        return row

    monkeypatch.setattr(_web_passkey, "passkey_vault_configured", lambda: configured)
    monkeypatch.setattr(_web_passkey, "fetch_account_passkey", _fetch)


@pytest.mark.asyncio
@pytest.mark.parametrize(("configured", "row"), [(False, _ROW), (True, None)])
async def test_no_signer_without_a_vault_or_a_passkey(
    monkeypatch: pytest.MonkeyPatch, *, configured: bool, row: StoredPasskey | None
) -> None:
    _wire(monkeypatch, configured=configured, row=row)

    assert await _web_passkey.web_passkey_signer("acc-1") is None


@pytest.mark.asyncio
async def test_the_signer_signs_the_assertion_in_the_vault_for_this_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(monkeypatch, configured=True, row=_ROW)
    signed: list[tuple[str, bytes]] = []

    async def _sign(key_name: str, payload: bytes) -> bytes:
        signed.append((key_name, payload))
        return b"DER-SIGNATURE"

    monkeypatch.setattr(_web_passkey, "sign_with_key", _sign)
    signer = await _web_passkey.web_passkey_signer("acc-1")
    assert signer is not None

    out = await signer("Y2hhbGxlbmdl")

    assert [name for name, _payload in signed] == ["tb-acc-1"]
    assert out.credential_id == "Y3JlZA"
    assert b64url_decode(out.signature) == b"DER-SIGNATURE"
    assert b64url_decode(out.user_handle) == b"1:257221571"
    client_data = json.loads(b64url_decode(out.client_data_json))
    assert client_data["type"] == "webauthn.get"
    assert client_data["challenge"] == "Y2hhbGxlbmdl"
    # What the vault signed is authenticatorData || SHA-256(clientDataJSON), as WebAuthn says.
    assert signed[0][1].startswith(b64url_decode(out.authenticator_data))
    assert len(signed[0][1]) == len(b64url_decode(out.authenticator_data)) + 32


@pytest.mark.asyncio
async def test_spawn_hands_the_window_launcher_the_signer_only_when_there_is_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[dict[str, Any]] = []

    async def _launch(_relay_port: int, **kwargs: Any) -> object:
        seen.append(kwargs)
        return object()

    async def _dressed(_account_id: str, _country: object, launch: Any) -> object:
        return await launch("fingerprint")

    monkeypatch.setattr(web_login, "launch_account_web", _launch)
    monkeypatch.setattr(web_login, "launch_dressed", _dressed)
    monkeypatch.setattr(web_login, "watch_window", lambda *_a, **_k: None)
    monkeypatch.setattr(web_login, "_windows", {})
    sentinel = object()

    for signer in (None, sentinel):

        async def _signer(_account_id: str, _s: object = signer) -> object:
            return _s

        monkeypatch.setattr(web_login, "web_passkey_signer", _signer)
        await web_login._spawn("acc-1", 1, tmp_path, None, capture=False)

    assert "passkey_signer" not in seen[0]
    assert seen[1]["passkey_signer"] is sentinel
