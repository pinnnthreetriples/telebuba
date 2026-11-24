"""Consistent snapshots of ``telebuba.db``: the periodic backup and the one before a migration.

The database holds every account's 2FA password, device fingerprint and proxy binding, so a
schema change never runs on it without a copy beside it. Both writers share the staging and
publish rules here: ``VACUUM INTO`` a ``.part`` file inside a private directory, then an atomic
rename onto the real name, so only a complete file is ever counted as a backup.

Takes the connection as an argument and imports nothing from ``core.db`` (which imports this
module), so ``core.db`` and ``core.db_maintenance`` can both use it without an import cycle.
"""

from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import text

from core.config import settings
from core.db_mirror import mirror_snapshot
from core.migrations import MIGRATIONS, apply_migrations, ensure_schema_not_newer
from core.secure_paths import make_private_dir, make_private_file

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from sqlalchemy.engine import Connection, Engine

__all__ = [
    "BACKUP_GLOB",
    "default_clock",
    "newest_backup_at",
    "prepare_schema",
    "prune_backups",
    "snapshot_before_migrations",
    "vacuum_into",
    "write_snapshot",
]

BACKUP_STEM = "telebuba"
BACKUP_SUFFIX = ".db"
BACKUP_GLOB = f"{BACKUP_STEM}-*{BACKUP_SUFFIX}"
# The vacuum writes here and is renamed onto its real name only on success, so a
# failed run (disk full) cannot leave a half-written file that ``prune_backups``
# would count as a backup — and, being the newest, keep in place of a good one.
PARTIAL_SUFFIX = ".part"
TIMESTAMP_FORMAT = "%Y%m%dT%H%M%S%fZ"


def default_clock() -> datetime:
    return datetime.now(UTC)


def vacuum_into(connection: Connection, path: Path) -> None:
    """Copy a consistent snapshot to ``path``. The path is bound, never interpolated."""
    connection.execute(text("VACUUM INTO :path"), {"path": str(path)})


def write_snapshot(
    connection: Connection,
    backup_dir: Path,
    *,
    clock: Callable[[], datetime] = default_clock,
    vacuum: Callable[[Connection, Path], None] = vacuum_into,
) -> Path:
    """Write one snapshot into ``backup_dir`` and publish it; the published path.

    ``vacuum`` is the copy step, a parameter so ``core.db_maintenance`` keeps its seam.
    """
    # A backup is a byte-for-byte copy of the credential store — plaintext proxy
    # passwords and every password_hash — so it needs the same 0700/0600 as the
    # database it copies. Restricting the dir also covers the ``.part`` staging
    # file, which holds the same bytes before it is published.
    make_private_dir(backup_dir)
    stamp = clock().strftime(TIMESTAMP_FORMAT)
    target = backup_dir / f"{BACKUP_STEM}-{stamp}{BACKUP_SUFFIX}"
    partial = target.with_name(target.name + PARTIAL_SUFFIX)
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
    # foreign in-flight .part is only safe because snapshots are single-process:
    # one uvicorn worker and one task, whose runs serialise on ``asyncio.to_thread``.
    for orphan in backup_dir.glob(f"{BACKUP_GLOB}{PARTIAL_SUFFIX}"):
        with suppress(OSError):
            orphan.unlink(missing_ok=True)
    vacuum(connection, partial)
    partial.replace(target)  # atomic publish: only a complete file gets the real name.
    make_private_file(target)
    mirror_snapshot(target)  # encrypted copy off the disk; never raises (see core.db_mirror)
    return target


def prune_backups(backup_dir: Path, keep: int, *, written: Path | None = None) -> None:
    """Keep the newest ``keep`` backups. A ``.part`` is never ranked: the glob cannot match it.

    ``written`` is the backup this run just published: it is never pruned, whatever else the
    folder holds. Names rank by their stamp, so files stamped in the future (a clock that
    jumped back) would otherwise outrank it and push it out the moment it was written.
    """
    backups = sorted(path for path in backup_dir.glob(BACKUP_GLOB) if path != written)
    if written is not None:
        keep = max(0, keep - 1)
    # max(0, ...): a negative count is not "delete nothing" in a slice, it means
    # "all but the last N" — under the limit that deleted the oldest backups.
    excess = max(0, len(backups) - keep)
    # Retention is best-effort: it must never cost a backup that already published, and
    # one un-removable old file must not stop the rest going. ``excess`` is recomputed
    # every run, so a transient handle self-heals.
    for stale in backups[:excess]:
        with suppress(OSError):
            stale.unlink(missing_ok=True)


def newest_backup_at(backup_dir: Path, *, now: datetime | None = None) -> datetime | None:
    """When the newest published backup was taken, from its file name; ``None`` if none.

    A stamp in the future is not a backup of anything that exists yet (the clock jumped back,
    or the file was copied in): it is skipped, or it would postpone the next run by the skew.
    """
    if not backup_dir.is_dir():
        return None
    moment = now or default_clock()
    for path in sorted(backup_dir.glob(BACKUP_GLOB), reverse=True):
        stamp = path.name.removeprefix(f"{BACKUP_STEM}-").removesuffix(BACKUP_SUFFIX)
        try:
            taken = datetime.strptime(stamp, TIMESTAMP_FORMAT).replace(tzinfo=UTC)
        except ValueError:
            continue
        if taken <= moment:
            return taken
    return None


def _needs_snapshot(connection: Connection) -> bool:
    """An existing database (it has tables) with at least one migration not yet stamped."""
    tables = {
        str(row[0])
        for row in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if not tables:
        return False  # a fresh file holds nothing to lose.
    if "schema_version" not in tables:
        return True
    applied = {
        int(row[0]) for row in connection.exec_driver_sql("SELECT version FROM schema_version")
    }
    return any(version not in applied for version, _name, _body in MIGRATIONS)


def snapshot_before_migrations(
    engine: Engine, *, clock: Callable[[], datetime] = default_clock
) -> Path | None:
    """Snapshot an existing database that is about to be migrated; the path, or ``None``.

    Fails closed: a snapshot that cannot be written raises, and the caller does not
    migrate. This is the credential store, and a migration cannot be taken back.
    """
    if not settings.db.backup_enabled:
        return None
    with engine.connect() as connection:
        if not _needs_snapshot(connection):
            return None
        written = write_snapshot(connection, settings.db.backup_dir, clock=clock)
    prune_backups(settings.db.backup_dir, settings.db.backup_keep, written=written)
    return written


def prepare_schema(engine: Engine, create_tables: Callable[[Engine], None]) -> None:
    """Snapshot, create the tables and migrate; dispose ``engine`` and re-raise on any failure.

    The caller publishes the engine only after this returns, so a failed start leaves no
    half-migrated engine behind and the next ``_get_engine()`` retries from scratch.
    """
    try:
        ensure_schema_not_newer(engine)
        snapshot_before_migrations(engine)
        create_tables(engine)
        apply_migrations(engine)
    except BaseException:
        engine.dispose()
        raise
