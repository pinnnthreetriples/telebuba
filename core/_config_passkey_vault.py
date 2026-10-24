"""The key vault that holds each account's Telegram passkey (OpenBao / Vault Transit).

Re-exported through ``core.config`` like ``core._config_telephony``. The private half
of every passkey is a non-exportable ``ecdsa-p256`` key inside the vault's Transit
engine; Telebuba only ever sees the public key and the signatures it asks for. An
empty ``token`` turns the feature off: every passkey route then refuses with
``passkey_vault_disabled`` instead of failing on a connect.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class PasskeyVaultSettings(BaseSettings):
    """Where the vault listens, the token Telebuba uses, and its Transit mount."""

    model_config = SettingsConfigDict(env_prefix="PASSKEY_VAULT__", extra="ignore")

    addr: str = Field(default="http://127.0.0.1:8200", min_length=1)
    # The app token: its policy may touch only ``<mount>/keys/<prefix>*`` and
    # ``<mount>/sign/<prefix>*``. Empty disables passkeys.
    token: str = Field(default="", repr=False)
    mount: str = Field(default="transit", pattern=r"^[A-Za-z0-9_-]+$")
    # Every key Telebuba creates is named ``<prefix><account_id>``.
    key_prefix: str = Field(default="tb-", pattern=r"^[A-Za-z0-9_-]+$")
    timeout_seconds: float = Field(default=10.0, gt=0)
