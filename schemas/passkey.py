"""Telegram passkey models — the stored record and the API's view of it.

Nothing here is a secret: the private key never leaves the vault, and the credential
id and user handle are what Telegram itself sends any client that asks.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class StoredPasskey(BaseModel):
    """One account's passkey: which vault key signs for it, and how Telegram names it."""

    account_id: str = Field(min_length=1)
    credential_id: str = Field(min_length=1)
    # ``"<dc_id>:<user_id>"`` (Telegram's WebAuthn user handle).
    user_handle: str = Field(pattern=r"^[0-9]+:[0-9]+$")
    vault_key_name: str = Field(min_length=1)
    created_at: str


class WebPasskeyAssertion(BaseModel):
    """A browser's WebAuthn ``get`` answer for a vault-held passkey, binaries base64url.

    Serialised camelCase: it is spliced straight into the page script that resolves
    WebK's ``navigator.credentials.get`` call.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    credential_id: str
    client_data_json: str
    authenticator_data: str
    signature: str
    user_handle: str


class AccountPasskeyView(BaseModel):
    """Whether the account has a passkey, and whether passkeys can be used at all."""

    model_config = ConfigDict(extra="forbid")

    account_id: str
    has_passkey: bool
    created_at: str | None = None
    # False when no vault token is configured: every passkey action then refuses.
    vault_configured: bool
