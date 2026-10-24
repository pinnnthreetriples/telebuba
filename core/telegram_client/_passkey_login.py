"""Sign an account back in with its passkey — no SMS code, just the vault's signature.

The flow is ``auth.initPasskeyLogin`` → sign the challenge in the vault →
``auth.finishPasskeyLogin`` on the DC the passkey's user handle names, then the
stored 2FA password when Telegram asks for one. It runs on the account's OWN
``.session`` file, exactly like ``_auth.submit_phone_code``, under the same per-account
auth lock and pool tombstone, so it can repair a session Telegram revoked.

An already-authorized session is left alone and reported alive: Telegram refuses a
passkey login for the user a client is already signed in as. A sign-in that lands on
a different user is logged out again before anything is reported.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telethon import errors
from telethon.tl.functions.auth import FinishPasskeyLoginRequest, InitPasskeyLoginRequest
from telethon.tl.types import DataJSON, InputPasskeyCredentialPublicKey, InputPasskeyResponseLogin

from core.passkey_vault import PasskeyVaultError, sign_with_key
from core.repositories._accounts_twofa import fetch_account_twofa_password
from core.telegram_client._auth import _alive_result, _auth_lock, _error_result, _status_result
from core.telegram_client._client import create_telegram_client, prepare_telegram_client_profile
from core.telegram_client._passkey import PasskeyGatewayError, public_key_options
from core.telegram_client._pool import removing_client
from core.webauthn_passkey import build_assertion
from schemas.device_fingerprint import TelegramClientRequest

if TYPE_CHECKING:
    from telethon import TelegramClient

    from schemas.passkey import StoredPasskey
    from schemas.telegram_session import TelegramSessionCheckResult

# Stdlib sink: a transport failure stringifies with the proxy URL (see ``_pool``).
logger = logging.getLogger(__name__)
_INT64_SPAN = 1 << 64
_INT64_MAX = 1 << 63


def _signed64(value: int) -> int:
    """Telethon keeps auth key ids unsigned; the TL ``long`` field is signed."""
    return value - _INT64_SPAN if value >= _INT64_MAX else value


async def _signed_credential(
    passkey: StoredPasskey, challenge: str
) -> InputPasskeyCredentialPublicKey:
    assertion = build_assertion(challenge)
    try:
        signature = await sign_with_key(passkey.vault_key_name, assertion.signed_payload)
    except PasskeyVaultError as exc:
        raise PasskeyGatewayError(exc.code, error_type=exc.detail) from exc
    return InputPasskeyCredentialPublicKey(
        id=passkey.credential_id,
        raw_id=passkey.credential_id,
        response=InputPasskeyResponseLogin(
            client_data=DataJSON(data=assertion.client_data_json),
            authenticator_data=assertion.authenticator_data,
            signature=signature,
            user_handle=passkey.user_handle,
        ),
    )


async def _finish_login(client: TelegramClient, passkey: StoredPasskey) -> None:
    session = client.session
    if session is None or session.auth_key is None or session.auth_key.key_id is None:
        msg = "unavailable"
        raise PasskeyGatewayError(msg)
    init_dc = session.dc_id
    init_key_id = _signed64(session.auth_key.key_id)
    options = await client(InitPasskeyLoginRequest(api_id=client.api_id, api_hash=client.api_hash))
    challenge = str(public_key_options(options.options.data)["challenge"])
    credential = await _signed_credential(passkey, challenge)
    user_dc = int(passkey.user_handle.partition(":")[0])
    if user_dc == init_dc:
        request = FinishPasskeyLoginRequest(credential=credential)
    else:
        # The challenge was issued on ``init_dc``; the login completes on the user's
        # home DC, which is told where the challenge came from.
        await client._switch_dc(user_dc)  # noqa: SLF001 - Telethon has no public DC switch
        request = FinishPasskeyLoginRequest(
            credential=credential, from_dc_id=init_dc, from_auth_key_id=init_key_id
        )
    try:
        await client(request)
    except errors.SessionPasswordNeededError:
        password = await fetch_account_twofa_password(passkey.account_id)
        if not password:
            msg = "passkey_twofa_missing"
            raise PasskeyGatewayError(msg) from None
        await client.sign_in(password=password)


async def _log_out_quietly(client: TelegramClient) -> None:
    try:
        await client.log_out()
    except Exception as exc:  # noqa: BLE001 - best effort; the refusal is reported anyway.
        logger.warning("passkey sign-in logout failed (%s)", type(exc).__name__)


async def login_with_passkey(
    passkey: StoredPasskey, session_name: str | None
) -> TelegramSessionCheckResult:
    """Re-authorize the account's session with its passkey; the session verdict."""
    profile = await prepare_telegram_client_profile(
        TelegramClientRequest(
            account_id=passkey.account_id, session_name=session_name, receive_updates=False
        ),
    )
    user_id = int(passkey.user_handle.partition(":")[2])
    async with (
        _auth_lock(passkey.account_id),
        removing_client(passkey.account_id, profile.device_type),
    ):
        client = create_telegram_client(profile)
        try:
            await client.connect()
            if await client.is_user_authorized():
                return _alive_result(profile, await client.get_me())
            await _finish_login(client, passkey)
            me = await client.get_me()
            if getattr(me, "id", None) != user_id:
                await _log_out_quietly(client)
                return _status_result(
                    profile, status="unauthorized", error_message="passkey_user_mismatch"
                )
            return _alive_result(profile, me)
        except PasskeyGatewayError as exc:
            return _status_result(profile, status="unauthorized", error_message=exc.code)
        except errors.FloodWaitError as exc:
            return _error_result(
                profile,
                exc,
                status="flood_wait",
                is_temporary=True,
                flood_wait_seconds=exc.seconds,
            )
        except Exception as exc:  # noqa: BLE001 - classify any other SDK/network failure.
            logger.warning("passkey sign-in failed for %s", passkey.account_id, exc_info=exc)
            return _error_result(profile, exc, status="unknown_error", is_temporary=True)
        finally:
            await client.disconnect()
