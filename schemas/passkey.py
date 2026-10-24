"""Telegram passkey models — the stored record and the API's view of it.

Nothing here is a secret: the private key never leaves the vault, and the credential
id and user handle are what Telegram itself sends any client that asks.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class StoredPasskey(BaseModel):
    """One account's passkey: which vault key signs for it, and how Telegram names it."""

    account_id: str = Field(min_length=1)
    credential_id: str = Field(min_length=1)
    # ``"<dc_id>:<user_id>"`` (Telegram's WebAuthn user handle).
    user_handle: str = Field(pattern=r"^[0-9]+:[0-9]+$")
    vault_key_name: str = Field(min_length=1)
    created_at: str


class AccountPasskeyView(BaseModel):
    """Whether the account has a passkey, and whether passkeys can be used at all."""

    model_config = ConfigDict(extra="forbid")

    account_id: str
    has_passkey: bool
    created_at: str | None = None
    # False when no vault token is configured: every passkey action then refuses.
    vault_configured: bool
