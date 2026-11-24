"""The session guard's heartbeat, as the API serves it. No behaviour, no I/O."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Why the guard counts as stalled: its task ended outside a shutdown, or it has done
# nothing for longer than a step's deadline plus two passes.
SessionGuardStallReason = Literal["stopped", "silent"]


# The key vault's last known health (``core.passkey_vault.vault_health``).
PasskeyVaultHealth = Literal["off", "ok", "sealed", "unreachable", "rejected"]


class SessionBackupStatus(BaseModel):
    """Whether the safety nets exist and are fresh: key copies, database backups, key vault.

    Every field has a default so a client that predates the object keeps working.
    """

    session_vault_enabled: bool = False
    mirror_configured: bool = False
    accounts_total: int = 0
    # Accounts whose primary session has no version in the vault folder (all of them when
    # the vault is off).
    accounts_without_copy: int = 0
    # ISO time of the newest version file across the vault.
    last_session_copy_at: str | None = None
    db_backup_enabled: bool = False
    # ISO time of the newest database backup, from its file name.
    last_db_backup_at: str | None = None
    # Enabled, and the newest backup is older than two intervals or there is none.
    db_backup_overdue: bool = False
    # Encrypted database copies in the mirror's ``db/`` folder (needs the vault key and a mirror).
    db_mirror_enabled: bool = False
    # ISO time of the newest ``.db.enc`` there, from its file name.
    db_mirror_last_at: str | None = None
    passkey_vault: PasskeyVaultHealth = "off"


class SessionGuardStatus(BaseModel):
    """Whether the guard runs, when it last did something, and whether that is too long ago."""

    running: bool
    last_activity_at: str | None = None
    stalled: bool = False
    reason: SessionGuardStallReason | None = None
    backups: SessionBackupStatus = Field(default_factory=SessionBackupStatus)
