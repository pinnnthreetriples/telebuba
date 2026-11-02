"""The signer behind a web window's «Log in by passkey», split out of ``web_login``.

An account window answers WebK's passkey prompt from the account's own vault key, so
signing is offered only when the vault is configured and the account has a passkey. The
returned callable closes over that one passkey: a window can never be made to sign for
another account.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.passkey_vault import passkey_vault_configured, sign_with_key
from core.repositories._accounts_passkey import fetch_account_passkey
from core.webauthn_passkey import b64url, build_assertion
from schemas.passkey import WebPasskeyAssertion

if TYPE_CHECKING:
    from core.web_login._passkey import PasskeySigner


async def web_passkey_signer(account_id: str) -> PasskeySigner | None:
    """A signer for the account's passkey, or ``None`` when it has none to offer."""
    if not passkey_vault_configured():
        return None
    passkey = await fetch_account_passkey(account_id)
    if passkey is None:
        return None

    async def sign(challenge: str) -> WebPasskeyAssertion:
        assertion = build_assertion(challenge)
        signature = await sign_with_key(passkey.vault_key_name, assertion.signed_payload)
        return WebPasskeyAssertion(
            credential_id=passkey.credential_id,
            client_data_json=b64url(assertion.client_data_json.encode()),
            authenticator_data=b64url(assertion.authenticator_data),
            signature=b64url(signature),
            user_handle=b64url(passkey.user_handle.encode()),
        )

    return sign
