"""SQLAlchemy table for each account's Telegram passkey (one per account).

Its own module because ``core._schema_tables`` is near the file-size budget. Mirrors
``core.migration_steps_passkeys``: ``create_all`` builds a fresh database from THIS
module while an existing one gets migration 100, and
``tests/core/test_migrations_passkeys.py`` compares the two.

No secret lives here: the private key is in the vault (``vault_key_name``), and the
credential id and user handle are what Telegram hands any client that asks.
"""

from __future__ import annotations

from sqlalchemy import Column, String, Table

from core._schema_tables import _metadata

_account_passkeys = Table(
    "account_passkeys",
    _metadata,
    Column("account_id", String, primary_key=True),
    # base64url, exactly as ``inputPasskeyCredentialPublicKey.id`` carries it.
    Column("credential_id", String, nullable=False),
    # ``"<dc_id>:<user_id>"``: Telegram's WebAuthn user handle, which names the DC
    # a passkey login must finish on.
    Column("user_handle", String, nullable=False),
    Column("vault_key_name", String, nullable=False),
    Column("created_at", String, nullable=False),
)
