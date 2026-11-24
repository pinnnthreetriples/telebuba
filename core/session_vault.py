"""Encrypted, versioned copies of ``.session`` files, and the quarantine removed files go to.

Why: the ``.session`` file IS the account (its only auth key). Telebuba kept one copy; a
deleted account, a reset, a swap that went wrong or a dead disk lost the key for good.

* :func:`snapshot_session` reads a session file with SQLite's online backup (a pooled
  client may hold it open), encrypts the bytes with Fernet and writes them as a new
  version, ``<dir>/<file stem>/<UTC stamp>-<sha12>.session.enc`` (and the same into the
  mirror folder when one is set). An unchanged file is not copied again. The first version
  is kept forever, plus the newest ``keep_versions``.
* :func:`restore_session` decrypts a version into a NEW file (never over an existing one).
* :func:`quarantine_file` moves a removed session file aside instead of deleting it;
  :func:`purge_quarantine` drops what is older than ``quarantine_days``.

Synchronous on purpose: callers run them in a worker thread. Nothing here logs the key,
the file content or its path text; failures raise ``OSError``/``sqlite3.Error`` or
:class:`SessionVaultError` with a bounded code.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from contextlib import closing, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from cryptography.fernet import Fernet, InvalidToken

from core.config import settings
from core.secure_paths import make_private_dir, make_private_file

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

__all__ = [
    "CopyOverview",
    "SessionVaultError",
    "copy_overview",
    "list_versions",
    "new_vault_key",
    "purge_quarantine",
    "quarantine_file",
    "restore_session",
    "snapshot_session",
    "vault_enabled",
]

SessionVaultCode = Literal["vault_key_invalid", "vault_target_exists", "vault_copy_unreadable"]
_SUFFIX = ".session.enc"
_STAMP = "%Y%m%dT%H%M%S%fZ"
_DIGEST_CHARS = 12
_SECONDS_PER_DAY = 86_400


class SessionVaultError(Exception):
    """The vault refused; ``code`` is the stable contract."""

    def __init__(self, code: SessionVaultCode) -> None:
        super().__init__(code)
        self.code: SessionVaultCode = code


def vault_enabled() -> bool:
    return bool(settings.session_vault.key)


def new_vault_key() -> str:
    """A fresh key for ``SESSION_VAULT__KEY``."""
    return Fernet.generate_key().decode()


def _fernet() -> Fernet:
    try:
        return Fernet(settings.session_vault.key.encode())
    except (ValueError, TypeError) as exc:
        code = "vault_key_invalid"
        raise SessionVaultError(code) from exc


def _read_consistent(session_file: Path) -> bytes:
    """The file's content through SQLite's own backup: consistent even while it is in use."""
    with (
        closing(sqlite3.connect(f"{session_file.resolve().as_uri()}?mode=ro", uri=True)) as src,
        closing(sqlite3.connect(":memory:")) as copy,
    ):
        src.backup(copy)
        return copy.serialize()


def _versions(folder: Path) -> list[Path]:
    return sorted(folder.glob(f"*{_SUFFIX}")) if folder.is_dir() else []


def _write_version(folder: Path, name: str, payload: bytes) -> Path:
    make_private_dir(folder)
    target = folder / name
    partial = folder / f"{name}.part"
    partial.write_bytes(payload)
    partial.replace(target)
    make_private_file(target)
    return target


def _prune(folder: Path) -> None:
    versions = _versions(folder)
    keep = settings.session_vault.keep_versions
    # The first version is the account as it was bought: never pruned.
    for stale in versions[1:-keep] if len(versions) > keep + 1 else []:
        stale.unlink(missing_ok=True)


def _sweep_partials(folder: Path) -> None:
    """Remove ``.part`` leftovers of a write that died; best-effort, never a reason to fail."""
    if not folder.is_dir():
        return
    for orphan in folder.glob(f"*{_SUFFIX}.part"):
        with suppress(OSError):
            orphan.unlink(missing_ok=True)


def _reconcile_mirror(folder: Path, mirror_folder: Path) -> None:
    """Copy every version the primary holds and the mirror lacks, then prune the mirror.

    The encrypted bytes go over as they are. A mirror write that failed once (a disk that
    was away) would otherwise leave that version out of the mirror for good, since the
    primary dedupes against itself only.
    """
    _sweep_partials(mirror_folder)
    for version in _versions(folder):
        if not (mirror_folder / version.name).exists():
            _write_version(mirror_folder, version.name, version.read_bytes())
    _prune(mirror_folder)


def snapshot_session(session_file: Path, *, now: datetime | None = None) -> Path | None:
    """Store an encrypted copy of ``session_file``; the new version, or ``None`` if unchanged.

    ``None`` too when the vault is off or the file does not exist. Every call also brings the
    mirror level with the primary. The primary version is written first, so a mirror failure
    raises (the caller retries) without costing the primary copy.
    """
    if not vault_enabled() or not session_file.is_file():
        return None
    data = _read_consistent(session_file)
    digest = hashlib.sha256(data).hexdigest()[:_DIGEST_CHARS]
    folder = settings.session_vault.dir / session_file.stem
    _sweep_partials(folder)
    existing = _versions(folder)
    written: Path | None = None
    if not (existing and existing[-1].name.endswith(f"-{digest}{_SUFFIX}")):
        payload = _fernet().encrypt(data)
        stamp = (now or datetime.now(UTC)).strftime(_STAMP)
        written = _write_version(folder, f"{stamp}-{digest}{_SUFFIX}", payload)
        _prune(folder)
    mirror = settings.session_vault.mirror_dir
    if mirror is not None:
        _reconcile_mirror(folder, mirror / session_file.stem)
    return written


@dataclass(frozen=True)
class CopyOverview:
    """What the vault holds, for the status page: who has no copy, and when the newest was made."""

    without_copy: int
    newest_at: datetime | None


def _version_at(version: Path) -> datetime | None:
    """When a version was made, from its file name (``<stamp>-<sha12>.session.enc``)."""
    try:
        return datetime.strptime(version.name.partition("-")[0], _STAMP).replace(tzinfo=UTC)
    except ValueError:
        return None


def copy_overview(session_stems: Sequence[str]) -> CopyOverview:
    """How many of ``session_stems`` have no version at all, and the newest version's time.

    The newest is taken across the whole vault folder, not only ``session_stems``. File work:
    call it from a worker thread. Reads names only, never a copy's content.
    """
    root = settings.session_vault.dir
    without = sum(1 for stem in session_stems if not _versions(root / stem))
    stamps: list[datetime] = []
    for folder in sorted(root.iterdir()) if root.is_dir() else []:
        versions = _versions(folder)
        newest = _version_at(versions[-1]) if versions else None
        if newest is not None:
            stamps.append(newest)
    return CopyOverview(without_copy=without, newest_at=max(stamps, default=None))


def list_versions(session_stem: str) -> list[Path]:
    """The stored versions of one session file, oldest first."""
    return _versions(settings.session_vault.dir / session_stem)


def restore_session(version: Path, target: Path) -> None:
    """Decrypt ``version`` into ``target``, which must not exist yet."""
    if target.exists():
        code = "vault_target_exists"
        raise SessionVaultError(code)
    try:
        data = _fernet().decrypt(version.read_bytes())
    except InvalidToken as exc:
        code = "vault_copy_unreadable"
        raise SessionVaultError(code) from exc
    with (
        closing(sqlite3.connect(":memory:")) as copy,
        closing(sqlite3.connect(target)) as out,
    ):
        copy.deserialize(data)
        copy.backup(out)
    make_private_file(target)


def quarantine_file(path: Path, *, now: datetime | None = None) -> Path | None:
    """Move ``path`` into the quarantine instead of deleting it; ``None`` if it is not there."""
    if not path.exists():
        return None
    folder = settings.session_vault.quarantine_dir
    make_private_dir(folder)
    stamp = (now or datetime.now(UTC)).strftime(_STAMP)
    target = folder / f"{stamp}-{path.name}"
    path.replace(target)
    return target


def _quarantined_at(entry: Path) -> float | None:
    """When ``entry`` was quarantined, from its name (a move keeps the file's own mtime)."""
    stamp, _, _rest = entry.name.partition("-")
    try:
        return datetime.strptime(stamp, _STAMP).replace(tzinfo=UTC).timestamp()
    except ValueError:
        return None


def purge_quarantine(*, now: float | None = None) -> int:
    """Delete quarantined files older than ``quarantine_days``; how many went."""
    folder = settings.session_vault.quarantine_dir
    if not folder.is_dir():
        return 0
    limit = (now or time.time()) - settings.session_vault.quarantine_days * _SECONDS_PER_DAY
    purged = 0
    for entry in folder.iterdir():
        moved_at = _quarantined_at(entry)
        if entry.is_file() and moved_at is not None and moved_at < limit:
            entry.unlink(missing_ok=True)
            purged += 1
    return purged
