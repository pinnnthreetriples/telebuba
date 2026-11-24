"""The safety nets' state for ``GET /session-guard``: key copies, database backups, key vault.

File-system work (the vault folders, the backup directory) runs in a worker thread. Reads
file names and counts only: never a copy's content, never a path in the answer.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from core.config import settings
from core.db_mirror import db_mirror_enabled, newest_mirror_at
from core.db_snapshot import newest_backup_at
from core.passkey_vault import vault_health
from core.repositories.accounts import list_accounts
from core.session_vault import CopyOverview, copy_overview, vault_enabled
from schemas.session_guard import SessionBackupStatus
from services.session_guard._vault_health import last_vault_state

# A backup is overdue once the newest is older than this many intervals (one missed run).
_OVERDUE_INTERVALS = 2
_SECONDS_PER_HOUR = 3600


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None


def _overview(stems: list[str]) -> CopyOverview:
    """The vault's state; a folder that cannot be read counts every account as without a copy."""
    try:
        return copy_overview(stems)
    except OSError:
        return CopyOverview(without_copy=len(stems), newest_at=None)


def _newest_db_backup() -> datetime | None:
    """The newest database backup; a directory that cannot be read counts as none (overdue)."""
    try:
        return newest_backup_at(settings.db.backup_dir)
    except OSError:
        return None


def _newest_db_mirror() -> datetime | None:
    try:
        return newest_mirror_at()
    except OSError:
        return None


async def read_backup_status() -> SessionBackupStatus:
    """Whether copies of the keys and of the database exist, and how fresh they are."""
    accounts = (await list_accounts()).accounts
    # The file a client opens: the stored session name, else the account id.
    stems = [account.session_name or account.account_id for account in accounts]
    enabled = vault_enabled()
    overview, newest_db, newest_mirror = await asyncio.gather(
        asyncio.to_thread(_overview, stems),
        asyncio.to_thread(_newest_db_backup),
        asyncio.to_thread(_newest_db_mirror),
    )
    db_enabled = settings.db.backup_enabled
    age_limit = _OVERDUE_INTERVALS * settings.db.backup_interval_hours * _SECONDS_PER_HOUR
    overdue = db_enabled and (
        newest_db is None or (datetime.now(UTC) - newest_db).total_seconds() > age_limit
    )
    return SessionBackupStatus(
        session_vault_enabled=enabled,
        mirror_configured=settings.session_vault.mirror_dir is not None,
        accounts_total=len(stems),
        # With the vault off no account has a usable copy, whatever an old folder still holds.
        accounts_without_copy=overview.without_copy if enabled else len(stems),
        last_session_copy_at=_iso(overview.newest_at),
        db_backup_enabled=db_enabled,
        last_db_backup_at=_iso(newest_db),
        db_backup_overdue=overdue,
        db_mirror_enabled=db_mirror_enabled(),
        db_mirror_last_at=_iso(newest_mirror),
        passkey_vault=last_vault_state() or await vault_health(),
    )
