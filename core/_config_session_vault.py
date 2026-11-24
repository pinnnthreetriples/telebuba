"""Session vault settings: encrypted copies of every account's ``.session``, and a quarantine.

Re-exported through ``core.config`` like ``core._config_session_audit``. A ``.session`` file
is the account: the only copy of its key. Telebuba used to keep exactly one, and deleting
an account, a disk failure or a bad swap lost it for good.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SessionVaultSettings(BaseSettings):
    """Where session copies go, how they are encrypted, how long removed files are kept."""

    model_config = SettingsConfigDict(env_prefix="SESSION_VAULT__", extra="ignore")

    # A Fernet key (urlsafe base64 of 32 bytes). Empty: no encrypted copies are made.
    # Generate one with ``python -m tools.session_vault new-key`` and keep a copy OUTSIDE
    # this computer (a password manager): without it the copies cannot be read.
    key: str = Field(default="", repr=False)
    # The encrypted copies, one folder per session file.
    dir: Path = Path("runtime/session_vault")
    # A second place every copy is also written to (another disk, a synced folder); empty:
    # none. The copies are encrypted, so a cloud folder is fine.
    mirror_dir: Path | None = None
    # Copies kept per session file besides the first one, which is always kept.
    keep_versions: int = Field(default=10, ge=1)
    # Each account's files are copied again at most this often by the background pass
    # (and at once after an import, a login or a re-key); an unchanged file is skipped.
    backup_interval_hours: float = Field(default=24.0, gt=0.0)
    # An account whose copy failed (a full disk, an unreachable mirror) is tried again after
    # this many minutes: not on every guard pass, and not only after a whole interval.
    retry_minutes: float = Field(default=30.0, gt=0.0)
    # A removed ``.session`` (account deletion, reset, a discarded sign-in) is moved here
    # instead of deleted, and purged after this many days.
    quarantine_dir: Path = Path("runtime/session_quarantine")
    quarantine_days: float = Field(default=30.0, gt=0.0)
