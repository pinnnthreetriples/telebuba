"""The web signer reads the account's CURRENT passkey for every request, and rides out a blip."""

from __future__ import annotations

import pytest

from core.passkey_vault import PasskeyVaultError
from core.webauthn_passkey import b64url_decode
from schemas.passkey import StoredPasskey
from services.accounts import _web_passkey

_ROW = StoredPasskey(
    account_id="acc-1",
    credential_id="Y3JlZA",
    user_handle="1:257221571",
    vault_key_name="tb-acc-1",
    created_at="2026-10-24T12:00:00+00:00",
)
_CHALLENGE = "Y2hhbGxlbmdl"


def _wire(monkeypatch: pytest.MonkeyPatch, rows: list[StoredPasskey | None]) -> None:
    async def _fetch(_account_id: str) -> StoredPasskey | None:
        return rows[0]

    monkeypatch.setattr(_web_passkey, "passkey_vault_configured", lambda: True)
    monkeypatch.setattr(_web_passkey, "fetch_account_passkey", _fetch)


@pytest.mark.asyncio
async def test_a_signer_is_offered_before_any_passkey_exists_and_reads_the_row_per_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A passkey made after the window opened must work in it (the hook was never installed)."""
    rows: list[StoredPasskey | None] = [None]
    _wire(monkeypatch, rows)

    async def _sign(key_name: str, _payload: bytes) -> bytes:
        return key_name.encode()

    monkeypatch.setattr(_web_passkey, "sign_with_key", _sign)
    signer = await _web_passkey.web_passkey_signer("acc-1")
    assert signer is not None

    with pytest.raises(_web_passkey.NoPasskeyError):
        await signer(_CHALLENGE)

    rows[0] = _ROW  # created after the window opened
    first = await signer(_CHALLENGE)
    rows[0] = _ROW.model_copy(update={"credential_id": "bmV3", "vault_key_name": "tb-new"})
    second = await signer(_CHALLENGE)  # recreated: the NEW key signs

    assert first.credential_id == "Y3JlZA"
    assert second.credential_id == "bmV3"
    assert b64url_decode(second.signature) == b"tb-new"


@pytest.mark.asyncio
async def test_a_briefly_unreachable_vault_is_retried_and_a_refusal_is_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(monkeypatch, [_ROW])
    monkeypatch.setattr(_web_passkey, "_SIGN_RETRY_SECONDS", 0)
    outcomes: list[PasskeyVaultError | bytes] = [
        PasskeyVaultError("passkey_vault_unreachable", "ConnectError"),
        PasskeyVaultError("passkey_vault_rejected", "http_503"),
        b"SIG",
    ]
    calls: list[int] = []

    async def _sign(_key: str, _payload: bytes) -> bytes:
        calls.append(1)
        outcome = outcomes.pop(0)
        if isinstance(outcome, PasskeyVaultError):
            raise outcome
        return outcome

    monkeypatch.setattr(_web_passkey, "sign_with_key", _sign)
    signer = await _web_passkey.web_passkey_signer("acc-1")
    assert signer is not None

    out = await signer(_CHALLENGE)

    assert b64url_decode(out.signature) == b"SIG"
    assert len(calls) == 3

    async def _refused(_key: str, _payload: bytes) -> bytes:
        calls.append(1)
        code = "passkey_vault_rejected"
        raise PasskeyVaultError(code, "http_403")

    monkeypatch.setattr(_web_passkey, "sign_with_key", _refused)
    calls.clear()
    with pytest.raises(PasskeyVaultError):
        await signer(_CHALLENGE)
    assert len(calls) == 1  # a refusal is final: no retry
