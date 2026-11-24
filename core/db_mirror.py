"""Encrypted copies of the database backups, written next to the session copies.

The database holds what a bare ``.session`` copy cannot bring back (2FA passwords, device
fingerprints, proxy bindings), and ``backups/`` sits on the same disk as the live file. When
the vault key AND ``SESSION_VAULT__MIRROR_DIR`` are set, every snapshot that
:func:`core.db_snapshot.write_snapshot` publishes is also written, Fernet-encrypted, to
``<mirror>/db/telebuba-<stamp>.db.enc`` (staged as ``.part``, renamed atomically, private modes)
and pruned to the same keep-count as the local copies. A plaintext database is NEVER written
to the mirror.

Core cannot log: a mirror failure never fails the snapshot, its exception type is queued here
and the maintenance loop (``core.db_maintenance``) drains it into ``db_mirror_failed``.
"""

from __future__ import annotations

import threading
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cryptography.fernet import InvalidToken

from core.config import settings
from core.secure_paths import make_private_dir, make_private_file
from core.session_vault import SessionVaultError, vault_cipher, vault_enabled

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "db_mirror_enabled",
    "drain_mirror_errors",
    "mirror_snapshot",
    "newest_mirror_at",
    "restore_db_copy",
]

_STEM = "telebuba"
_SUFFIX = ".db.enc"
_PARTIAL = ".part"
_STAMP = "%Y%m%dT%H%M%S%fZ"
_errors: list[str] = []
_errors_lock = threading.Lock()


def db_mirror_enabled() -> bool:
    return vault_enabled() and settings.session_vault.mirror_dir is not None


def _folder() -> Path | None:
    mirror = settings.session_vault.mirror_dir
    return None if mirror is None else mirror / "db"


def drain_mirror_errors() -> list[str]:
    """The exception type names of mirror writes that failed since the last call."""
    with _errors_lock:
        drained = list(_errors)
        _errors.clear()
    return drained


def _publish(folder: Path, name: str, payload: bytes) -> None:
    make_private_dir(folder)
    for orphan in folder.glob(f"*{_PARTIAL}"):
        with suppress(OSError):
            orphan.unlink(missing_ok=True)
    partial = folder / f"{name}{_PARTIAL}"
    partial.write_bytes(payload)
    partial.replace(folder / name)
    make_private_file(folder / name)


def _prune(folder: Path, keep: int, written: Path) -> None:
    others = sorted(path for path in folder.glob(f"{_STEM}-*{_SUFFIX}") if path != written)
    for stale in others[: max(0, len(others) - max(0, keep - 1))]:
        with suppress(OSError):
            stale.unlink(missing_ok=True)


def mirror_snapshot(snapshot: Path) -> Path | None:
    """Write the encrypted copy of ``snapshot`` into the mirror; never raises.

    ``None`` when the mirror is off or the write failed (the failure is queued for
    :func:`drain_mirror_errors`).
    """
    folder = _folder()
    if folder is None or not vault_enabled():
        return None
    try:
        name = snapshot.name.removesuffix(".db") + _SUFFIX
        _publish(folder, name, vault_cipher().encrypt(snapshot.read_bytes()))
        _prune(folder, settings.db.backup_keep, folder / name)
    except Exception as exc:  # noqa: BLE001 - a mirror failure must never cost the local snapshot.
        with _errors_lock:
            _errors.append(type(exc).__name__)
        return None
    return folder / name


def newest_mirror_at(*, now: datetime | None = None) -> datetime | None:
    """When the newest encrypted database copy was made, from its name; future names ignored."""
    folder = _folder()
    if folder is None or not folder.is_dir():
        return None
    moment = now or datetime.now(UTC)
    for path in sorted(folder.glob(f"{_STEM}-*{_SUFFIX}"), reverse=True):
        stamp = path.name.removeprefix(f"{_STEM}-").removesuffix(_SUFFIX)
        try:
            taken = datetime.strptime(stamp, _STAMP).replace(tzinfo=UTC)
        except ValueError:
            continue
        if taken <= moment:
            return taken
    return None


def restore_db_copy(copy: Path, target: Path) -> None:
    """Decrypt a ``.db.enc`` copy into ``target``, a NEW file (never over an existing one)."""
    if target.exists():
        code = "vault_target_exists"
        raise SessionVaultError(code)
    try:
        data = vault_cipher().decrypt(copy.read_bytes())
    except InvalidToken as exc:
        code = "vault_copy_unreadable"
        raise SessionVaultError(code) from exc
    with target.open("xb") as out:
        out.write(data)
    make_private_file(target)
