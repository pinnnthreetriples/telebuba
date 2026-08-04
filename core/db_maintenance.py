"""Periodic SQLite maintenance — split from ``core.db``.

Owns the WAL checkpoint, the ``VACUUM INTO`` backup with its staging/publish and
retention rules, and the loop that drives them on the configured interval. Split
out for the file-size budget; the public functions are re-exported by ``core.db``
so existing call sites are unaffected.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import text

from core.config import settings
from core.db import _get_engine

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Periodic SQLite maintenance — WAL checkpoint + optional online backup.
# WAL never truncates on its own under a long-lived pool, and telebuba.db is the
# sole datastore (incl. users/auth), so nothing otherwise guards against loss.
# The clock is injectable so the backup filename is deterministic under test.
# --------------------------------------------------------------------------- #
_BACKUP_STEM = "telebuba"
_BACKUP_SUFFIX = ".db"
# The vacuum writes here and is renamed onto its real name only on success, so a
# failed run (disk full) cannot leave a half-written file that _prune_backups
# would count as a backup — and, being the newest, keep in place of a good one.
_BACKUP_PARTIAL_SUFFIX = ".part"
_BACKUP_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%S%fZ"


def _default_backup_clock() -> datetime:
    return datetime.now(UTC)


def _vacuum_into(connection: Connection, path: Path) -> None:
    """Copy a consistent snapshot to ``path``. The path is bound, never interpolated."""
    connection.execute(text("VACUUM INTO :path"), {"path": str(path)})


def run_db_maintenance(*, clock: Callable[[], datetime] = _default_backup_clock) -> Path | None:
    """Checkpoint the WAL and, when enabled, write + prune a timestamped backup.

    Returns the backup file path when one was written, else ``None``. The
    ``PRAGMA wal_checkpoint(TRUNCATE)`` always runs; the ``VACUUM INTO`` backup
    is gated on ``settings.db.backup_enabled``.
    """
    engine = _get_engine()
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
        if not settings.db.backup_enabled:
            return None
        backup_dir = settings.db.backup_dir
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = clock().strftime(_BACKUP_TIMESTAMP_FORMAT)
        target = backup_dir / f"{_BACKUP_STEM}-{stamp}{_BACKUP_SUFFIX}"
        partial = target.with_name(target.name + _BACKUP_PARTIAL_SUFFIX)
        # Sweep on ENTRY, not after a successful publish: a run that raises never
        # gets that far, and every run stamps a fresh name, so partials would pile
        # up one full-DB-sized file per interval on the volume holding the sole
        # datastore. VACUUM INTO holds no long lock, so the copy is safe online.
        # The glob covers this run's own output name too, so it needs no unlink of
        # its own. Leftovers are best-effort hygiene, so a stuck one (a live handle,
        # the path turned into a directory, EPERM/EBUSY) is suppressed — unguarded it
        # raises before the vacuum and no backup is ever taken again while it
        # persists. Suppressing our own path costs nothing: VACUUM INTO fails on a
        # non-empty existing output, so the run still raises either way — from sqlite
        # there, and from the publish rename on an empty one. Deleting a
        # foreign in-flight .part is only safe because maintenance is single-process:
        # one uvicorn worker (a non-negotiable) and one task, whose runs serialise
        # on ``await asyncio.to_thread``.
        for orphan in backup_dir.glob(f"{_BACKUP_STEM}-*{_BACKUP_SUFFIX}{_BACKUP_PARTIAL_SUFFIX}"):
            with suppress(OSError):
                orphan.unlink(missing_ok=True)
        _vacuum_into(connection, partial)
    partial.replace(target)  # atomic publish: only a complete file gets the real name.
    _prune_backups(backup_dir)
    return target


def _prune_backups(backup_dir: Path) -> None:
    # A partial is never ranked as a backup: the glob below cannot match the
    # ``.part`` suffix. Sweeping them is the caller's job, on entry.
    backups = sorted(backup_dir.glob(f"{_BACKUP_STEM}-*{_BACKUP_SUFFIX}"))
    # max(0, ...): a negative count is not "delete nothing" in a slice, it means
    # "all but the last N" — under the limit that deleted the oldest backups.
    excess = max(0, len(backups) - settings.db.backup_keep)
    # Same best-effort rule as the entry sweep: retention must never cost a backup
    # that already published, and one un-removable old file must not stop the rest
    # going. ``excess`` is recomputed every run, so a transient handle self-heals.
    for stale in backups[:excess]:
        with suppress(OSError):
            stale.unlink(missing_ok=True)


async def run_db_maintenance_loop() -> None:
    """Run :func:`run_db_maintenance` on the configured interval until cancelled."""
    interval_seconds = settings.db.backup_interval_hours * 3600.0
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await asyncio.to_thread(run_db_maintenance)
        except Exception as exc:  # one bad run must not end maintenance for the process.
            logger.exception("db maintenance run failed; retrying next interval")
            # The stdlib logger above is file-only by design (see core/logging.py), so
            # without this the loop retries forever with no Logs row and no Sentry
            # event. This writes INTO the database whose maintenance just failed, so on
            # the headline scenario (disk full) the row insert fails too, log_event
            # swallows it, and the surface is Sentry — or, with no DSN configured,
            # debug.log alone. The structured event carries the exception type only, but
            # that is not containment: with a DSN set, sentry_sdk's default logging
            # integration ships this ERROR record's traceback and frame locals (the
            # backup paths) too. Paths to a private error tracker, no secrets.
            from core.logging import log_event  # noqa: PLC0415 - avoids an import cycle

            await log_event(
                "ERROR",
                "db_maintenance_failed",
                extra={"error": type(exc).__name__},
            )
