"""Telegram passkey registration and removal on an account's live session.

Telegram's passkey API (``core.telegram.org/api/passkeys``) is WebAuthn: the server
hands out a challenge, the client answers with an authenticator's response. Here the
"authenticator" is a non-exportable key in the vault (``core.passkey_vault``) and the
WebAuthn bytes come from ``core.webauthn_passkey``.

Both calls borrow the pooled client, like ``_second_device`` reads the primary
session, rather than joining ``TelegramAction``: registration needs the vault's
public key in the middle of the exchange and returns Telegram's user handle, which
the action union has no shape for. Every failure becomes :class:`PasskeyGatewayError`
with a bounded code, so ``services`` never sees a Telethon error.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Literal

from telethon import errors
from telethon.tl.functions.account import (
    DeletePasskeyRequest,
    GetPasskeysRequest,
    InitPasskeyRegistrationRequest,
    RegisterPasskeyRequest,
)
from telethon.tl.types import (
    DataJSON,
    InputPasskeyCredentialPublicKey,
    InputPasskeyResponseRegister,
)

from core.telegram_client._pool import TelegramClientPoolError, get_client
from core.webauthn_passkey import b64url_decode, build_registration

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from telethon import TelegramClient

PasskeyGatewayCode = Literal[
    "flood_wait",
    "unavailable",
    "passkey_options_invalid",
    "passkey_telegram_refused",
    "passkey_twofa_missing",
    "passkey_user_mismatch",
    "passkey_vault_disabled",
    "passkey_vault_unreachable",
    "passkey_vault_rejected",
]


class PasskeyGatewayError(Exception):
    """A passkey step was refused; ``str(exc)`` is the stable code, never Telethon prose."""

    def __init__(
        self,
        code: PasskeyGatewayCode,
        *,
        retry_after_seconds: int | None = None,
        error_type: str | None = None,
    ) -> None:
        super().__init__(code)
        self.code: PasskeyGatewayCode = code
        self.retry_after_seconds = retry_after_seconds
        self.error_type = error_type


def public_key_options(options_json: str) -> dict[str, Any]:
    """The ``publicKey`` object of a Telegram passkey options payload."""
    try:
        public = json.loads(options_json).get("publicKey")
    except (ValueError, AttributeError) as exc:
        msg = "passkey_options_invalid"
        raise PasskeyGatewayError(msg) from exc
    if not isinstance(public, dict) or not isinstance(public.get("challenge"), str):
        msg = "passkey_options_invalid"
        raise PasskeyGatewayError(msg)
    return public


def _user_handle(public: dict[str, Any]) -> str:
    """Telegram's user handle (``"<dc_id>:<user_id>"``) from registration options."""
    user = public.get("user")
    encoded = user.get("id") if isinstance(user, dict) else None
    try:
        handle = b64url_decode(encoded).decode() if isinstance(encoded, str) else ""
    except ValueError as exc:
        msg = "passkey_options_invalid"
        raise PasskeyGatewayError(msg) from exc
    dc_id, _, user_id = handle.partition(":")
    if not (dc_id.isdecimal() and user_id.isdecimal()):
        msg = "passkey_options_invalid"
        raise PasskeyGatewayError(msg)
    return handle


async def _borrowed[T](account_id: str, call: Callable[[TelegramClient], Awaitable[T]]) -> T:
    """Run ``call`` on the account's pooled client, every failure as a bounded code."""
    try:
        client = await get_client(account_id)
        return await call(client)
    except PasskeyGatewayError:
        raise
    except errors.FloodWaitError as exc:
        msg = "flood_wait"
        raise PasskeyGatewayError(msg, retry_after_seconds=exc.seconds) from exc
    except (TelegramClientPoolError, ConnectionError, TimeoutError) as exc:
        msg = "unavailable"
        raise PasskeyGatewayError(msg, error_type=type(exc).__name__) from exc
    except errors.RPCError as exc:
        msg = "passkey_telegram_refused"
        raise PasskeyGatewayError(msg, error_type=type(exc).__name__) from exc


async def register_passkey(account_id: str, *, public_key_pem: str, credential_id: str) -> str:
    """Add a passkey for ``public_key_pem`` to the account; return Telegram's user handle."""

    async def _register(client: TelegramClient) -> str:
        options = await client(InitPasskeyRegistrationRequest())
        public = public_key_options(options.options.data)
        handle = _user_handle(public)
        registration = build_registration(str(public["challenge"]), public_key_pem, credential_id)
        credential = InputPasskeyCredentialPublicKey(
            id=credential_id,
            raw_id=credential_id,
            response=InputPasskeyResponseRegister(
                client_data=DataJSON(data=registration.client_data_json),
                attestation_data=registration.attestation_object,
            ),
        )
        await client(RegisterPasskeyRequest(credential=credential))
        return handle

    return await _borrowed(account_id, _register)


async def delete_passkey(account_id: str, credential_id: str) -> bool:
    """Remove the account's passkey ``credential_id``; whether Telegram still had it."""

    async def _delete(client: TelegramClient) -> bool:
        listed = await client(GetPasskeysRequest())
        found = [p.id for p in listed.passkeys if p.id == credential_id]
        for passkey_id in found:
            await client(DeletePasskeyRequest(id=passkey_id))
        return bool(found)

    return await _borrowed(account_id, _delete)
