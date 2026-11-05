"""The signer behind a web window's «Log in by passkey», split out of ``web_login``.

An account window answers WebK's passkey prompt from the account's own vault key, so
signing is offered only when the vault is configured. The passkey itself is read PER
REQUEST: a passkey created or recreated after the window opened (the window outlives
many clicks) signs with the current row, and one removed since fails the request instead
of signing with a key that is gone. The returned callable closes over the account id
only: a window can never be made to sign for another account.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from core.passkey_vault import PasskeyVaultError, passkey_vault_configured, sign_with_key
from core.repositories._accounts_passkey import fetch_account_passkey
from core.webauthn_passkey import b64url, build_assertion
from schemas.passkey import WebPasskeyAssertion

if TYPE_CHECKING:
    from core.web_login._passkey import PasskeySigner


# A vault that is briefly unreachable (or answering 5xx) must not cost the login: the
# signature is the whole of the passkey path, and a refusal here loses the account's window.
_SIGN_ATTEMPTS = 3
_SIGN_RETRY_SECONDS = 0.5


def _vault_hiccup(exc: PasskeyVaultError) -> bool:
    return exc.code == "passkey_vault_unreachable" or str(exc.detail).startswith("http_5")


async def _sign_with_retry(key_name: str, payload: bytes) -> bytes:
    for attempt in range(_SIGN_ATTEMPTS):
        try:
            return await sign_with_key(key_name, payload)
        except PasskeyVaultError as exc:
            if attempt == _SIGN_ATTEMPTS - 1 or not _vault_hiccup(exc):
                raise
            await asyncio.sleep(_SIGN_RETRY_SECONDS)
    raise AssertionError  # pragma: no cover - the loop returns or raises


class NoPasskeyError(LookupError):
    """The account has no passkey right now; the page is told the request was refused."""


async def web_passkey_signer(account_id: str) -> PasskeySigner | None:
    """A signer for the account's passkey, or ``None`` when there is no vault to sign in.

    Not gated on a passkey existing at launch: that check ran once, so a passkey made
    after the window opened could never be used in it.
    """
    if not passkey_vault_configured():
        return None

    async def sign(challenge: str) -> WebPasskeyAssertion:
        passkey = await fetch_account_passkey(account_id)
        if passkey is None:
            raise NoPasskeyError(account_id)
        assertion = build_assertion(challenge)
        signature = await _sign_with_retry(passkey.vault_key_name, assertion.signed_payload)
        return WebPasskeyAssertion(
            credential_id=passkey.credential_id,
            client_data_json=b64url(assertion.client_data_json.encode()),
            authenticator_data=b64url(assertion.authenticator_data),
            signature=b64url(signature),
            user_handle=b64url(passkey.user_handle.encode()),
        )

    return sign
