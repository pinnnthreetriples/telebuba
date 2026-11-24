"""Periodic SQLite maintenance — split from ``core.db``.

Owns the WAL checkpoint, the ``VACUUM INTO`` backup with its staging/publish and
retention rules, and the loop that drives them on the configured interval. Split
out for the file-size budget; the public functions are re-exported by ``core.db``
so existing call sites are unaffected.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.config import settings
from core.db import _get_engine
from core.db_snapshot import (
    default_clock as _default_backup_clock,
)
from core.db_snapshot import (
    newest_backup_at,
    prune_backups,
    write_snapshot,
)
from core.db_snapshot import (
    vacuum_into as _vacuum_into,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

logger = logging.getLogger(__name__)

_SECONDS_PER_HOUR = 3600.0
_SECONDS_PER_MINUTE = 60.0


# --------------------------------------------------------------------------- #
# Periodic SQLite maintenance — WAL checkpoint + optional online backup.
# WAL never truncates on its own under a long-lived pool, and telebuba.db is the
# sole datastore (incl. users/auth), so nothing otherwise guards against loss.
# The clock is injectable so the backup filename is deterministic under test.
# The staging/publish/retention rules live in ``core.db_snapshot``, shared with the
# snapshot taken before a schema migration.
# --------------------------------------------------------------------------- #
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
        # ``_vacuum_into`` is looked up here, at call time: it is the seam tests patch.
        target = write_snapshot(
            connection, settings.db.backup_dir, clock=clock, vacuum=_vacuum_into
        )
    prune_backups(settings.db.backup_dir, settings.db.backup_keep, written=target)
    return target


def _seconds_until_first_run() -> float:
    """How long the loop waits before its first run.

    With backups on, a missing or stale newest backup (older than one interval) is due
    now: a process that restarts more often than the interval must still back up. Otherwise
    the next run is one interval after the newest backup (or after start, with backups off).
    """
    interval = settings.db.backup_interval_hours * _SECONDS_PER_HOUR
    if not settings.db.backup_enabled:
        return interval
    newest = newest_backup_at(settings.db.backup_dir)
    if newest is None:
        return 0.0
    age = (datetime.now(UTC) - newest).total_seconds()
    return max(0.0, interval - age)


async def run_db_maintenance_loop() -> None:
    """Run :func:`run_db_maintenance` until cancelled.

    The first run is due at once when the newest backup is missing or older than the
    interval; afterwards one interval after the last successful run, and after a failed run
    ``DB__BACKUP_RETRY_MINUTES`` later, not a whole interval.
    """
    interval_seconds = settings.db.backup_interval_hours * _SECONDS_PER_HOUR
    retry_seconds = settings.db.backup_retry_minutes * _SECONDS_PER_MINUTE
    try:
        delay = await asyncio.to_thread(_seconds_until_first_run)
    except Exception:  # an unreadable backup dir must not end the task before its first run.
        logger.exception("could not read the backup dir at start; backing up now")
        delay = 0.0
    while True:
        await asyncio.sleep(delay)
        try:
            await asyncio.to_thread(run_db_maintenance)
        except Exception as exc:  # one bad run must not end maintenance for the process.
            logger.exception("db maintenance run failed; retrying shortly")
            delay = retry_seconds
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
        else:
            delay = interval_seconds
