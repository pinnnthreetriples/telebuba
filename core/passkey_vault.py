"""Key-vault gateway — the only module that talks to OpenBao / Vault Transit.

Three operations on one named ``ecdsa-p256`` key per account: create it (and read
its public half back), sign with it, and delete it. The key is created
non-exportable, so its private half never leaves the vault — not to Telebuba, not to
an operator with this module's token.

Failures become :class:`PasskeyVaultError` with a bounded code. The token travels in
a header and the key name in the URL, and httpx quotes the URL in some error
messages, so nothing here keeps ``str(exc)`` — only the class name or HTTP status.
A fresh client per call: passkeys are created and used rarely.
"""

from __future__ import annotations

import base64
from typing import Any, Literal

import httpx

from core.config import settings

PasskeyVaultCode = Literal[
    "passkey_vault_disabled",
    "passkey_vault_unreachable",
    "passkey_vault_rejected",
]
_KEY_TYPE = "ecdsa-p256"
# Telegram holds version 1's public key and nothing else: a rotation would make the
# vault sign with a key Telegram never saw. Sign with it explicitly, and refuse a key
# that has been rotated (the app's policy denies rotation; root does not).
_KEY_VERSION = 1
_SIGNATURE_PREFIX_PARTS = 3  # ``vault:v<N>:<base64 DER>``


class PasskeyVaultError(Exception):
    """The vault refused or could not be reached; ``code`` is the stable contract."""

    def __init__(self, code: PasskeyVaultCode, detail: str | None = None) -> None:
        super().__init__(code)
        self.code: PasskeyVaultCode = code
        # The exception class name or ``http_<status>`` — never the response body.
        self.detail = detail


def passkey_vault_configured() -> bool:
    return bool(settings.passkey_vault.token)


def vault_key_name(account_id: str) -> str:
    return f"{settings.passkey_vault.key_prefix}{account_id}"


async def _request(method: str, path: str, body: dict[str, object] | None = None) -> httpx.Response:
    config = settings.passkey_vault
    if not config.token:
        msg = "passkey_vault_disabled"
        raise PasskeyVaultError(msg)
    url = f"{config.addr.rstrip('/')}/v1/{config.mount}/{path}"
    try:
        async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
            response = await client.request(
                method, url, json=body, headers={"X-Vault-Token": config.token}
            )
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        msg = "passkey_vault_unreachable"
        raise PasskeyVaultError(msg, type(exc).__name__) from exc
    if not response.is_success:
        msg = "passkey_vault_rejected"
        raise PasskeyVaultError(msg, f"http_{response.status_code}")
    return response


def _first_public_key(payload: Any) -> str:  # noqa: ANN401 - parsed JSON
    """The PEM public key of version 1 in a ``keys/<name>`` read; a rotated key is refused."""
    data = payload.get("data")
    keys = data.get("keys") if isinstance(data, dict) else None
    if not isinstance(keys, dict) or not keys:
        msg = "passkey_vault_rejected"
        raise PasskeyVaultError(msg, "no_key_versions")
    if set(keys) != {str(_KEY_VERSION)}:
        msg = "passkey_vault_rejected"
        raise PasskeyVaultError(msg, "key_rotated")
    first = keys[str(_KEY_VERSION)]
    pem = first.get("public_key") if isinstance(first, dict) else None
    if not isinstance(pem, str) or not pem:
        msg = "passkey_vault_rejected"
        raise PasskeyVaultError(msg, "no_public_key")
    return pem


async def create_signing_key(name: str) -> str:
    """Create the non-exportable P-256 key ``name`` (a no-op if it exists); its PEM public key."""
    await _request("POST", f"keys/{name}", {"type": _KEY_TYPE, "exportable": False})
    response = await _request("GET", f"keys/{name}")
    return _first_public_key(response.json())


async def sign_with_key(name: str, data: bytes) -> bytes:
    """ECDSA-P256-SHA256 signature of ``data`` under key ``name``, DER-encoded (WebAuthn ES256)."""
    response = await _request(
        "POST",
        f"sign/{name}/sha2-256",
        {
            "input": base64.b64encode(data).decode(),
            "marshaling_algorithm": "asn1",
            "key_version": _KEY_VERSION,
        },
    )
    data_field = response.json().get("data")
    signature = data_field.get("signature") if isinstance(data_field, dict) else None
    parts = signature.split(":", 2) if isinstance(signature, str) else []
    if len(parts) != _SIGNATURE_PREFIX_PARTS:
        msg = "passkey_vault_rejected"
        raise PasskeyVaultError(msg, "bad_signature_format")
    return base64.b64decode(parts[2])


async def delete_signing_key(name: str) -> None:
    """Delete key ``name``; Transit refuses a delete until the key allows one."""
    await _request("POST", f"keys/{name}/config", {"deletion_allowed": True})
    await _request("DELETE", f"keys/{name}")
