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
    "passkey_vault_sealed",
    "passkey_vault_forbidden",
    "passkey_vault_key_missing",
    "passkey_vault_rejected",
]
_KEY_TYPE = "ecdsa-p256"
# Telegram holds version 1's public key and nothing else: a rotation would make the
# vault sign with a key Telegram never saw. Sign with it explicitly, and refuse a key
# that has been rotated (the app's policy denies rotation; root does not).
_KEY_VERSION = 1
_STATUS_SEALED = 503
_STATUS_FORBIDDEN = (401, 403)
_STATUS_NOT_FOUND = 404
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
        raise PasskeyVaultError(_refusal_code(response), f"http_{response.status_code}")
    return response


def _json(response: httpx.Response) -> Any:  # noqa: ANN401 - parsed JSON
    """The body as JSON; a 200 that is not JSON is a refusal, never a raw ``ValueError``."""
    try:
        return response.json()
    except ValueError as exc:
        msg = "passkey_vault_rejected"
        raise PasskeyVaultError(msg, "not_json") from exc


def _is_missing_key(response: httpx.Response) -> bool:
    """A 404 that is Vault's "no such key" answer, not a wrong mount or address.

    Vault answers a missing key with a JSON ``{"errors": [...]}`` body; a missing mount
    says ``no handler for route`` and a wrong address answers with something else
    entirely. Only the first may be read as "the key is gone". The body is inspected,
    never kept.
    """
    try:
        body = response.json()
    except ValueError:
        return False
    errors = body.get("errors") if isinstance(body, dict) else None
    if not isinstance(errors, list):
        return False
    return not any("no handler for route" in str(item) for item in errors)


def _refusal_code(response: httpx.Response) -> PasskeyVaultCode:
    """The distinct code for the refusals an operator can act on, by HTTP status.

    503 is how a sealed (or standby) node answers every request; 401/403 a token or
    policy that no longer grants the call; 404 a Transit key that is gone, but only when
    the body is Vault's own missing-key answer (see :func:`_is_missing_key`).
    """
    status_code = response.status_code
    if status_code == _STATUS_SEALED:
        return "passkey_vault_sealed"
    if status_code in _STATUS_FORBIDDEN:
        return "passkey_vault_forbidden"
    if status_code == _STATUS_NOT_FOUND and _is_missing_key(response):
        return "passkey_vault_key_missing"
    return "passkey_vault_rejected"


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
    return _first_public_key(_json(response))


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
    body = _json(response)
    data_field = body.get("data") if isinstance(body, dict) else None
    signature = data_field.get("signature") if isinstance(data_field, dict) else None
    parts = signature.split(":", 2) if isinstance(signature, str) else []
    if len(parts) != _SIGNATURE_PREFIX_PARTS:
        msg = "passkey_vault_rejected"
        raise PasskeyVaultError(msg, "bad_signature_format")
    return base64.b64decode(parts[2])


async def delete_signing_key(name: str) -> None:
    """Delete key ``name``; Transit refuses a delete until the key allows one.

    A key that is already gone counts as deleted, so a retry after a half-done removal
    converges instead of failing forever. Vault does not answer every step for a missing
    key with a 404 (the config POST can be a 400), so any refusal is followed by a read:
    only a key that provably does not exist makes the removal done.
    """
    try:
        await _request("POST", f"keys/{name}/config", {"deletion_allowed": True})
        await _request("DELETE", f"keys/{name}")
    except PasskeyVaultError as exc:
        if exc.code == "passkey_vault_key_missing":
            return
        try:
            gone = not await signing_key_exists(name)
        except PasskeyVaultError:
            gone = False  # cannot be asked: the original refusal is the honest answer
        if not gone:
            raise


async def signing_key_exists(name: str) -> bool:
    """Whether the vault holds key ``name`` (a read; raises when the vault cannot answer).

    ``False`` only for Vault's own missing-key 404; a missing mount or a wrong address
    is ``passkey_vault_rejected``, not "gone". A sealed vault, a refused token or an unreachable
    node raise :class:`PasskeyVaultError` with their own code, so a health check can tell
    "the key is gone" from "the vault cannot be asked"; a rotated or malformed key
    raises ``passkey_vault_rejected``.
    """
    try:
        response = await _request("GET", f"keys/{name}")
    except PasskeyVaultError as exc:
        if exc.code == "passkey_vault_key_missing":
            return False
        raise
    _first_public_key(_json(response))  # a rotated key can never sign for Telegram
    return True
