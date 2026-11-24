"""A daily raft snapshot of the key vault (OpenBao), kept beside the database backups.

Optional and off by default: only with ``PASSKEY_VAULT__SNAPSHOT_TOKEN`` set. The snapshot is
``GET /v1/sys/storage/raft/snapshot`` streamed to ``<DB backup dir>/openbao-<stamp>.snap.part``
and renamed onto its name once complete; the newest :data:`KEEP` stay, and each is copied to
``<SESSION_VAULT__MIRROR_DIR>/openbao/`` when a mirror is set (the file is already encrypted by
the vault's barrier, so it goes as it is). Works only with the integrated-storage (raft)
backend. Failures raise :class:`PasskeyVaultError` carrying the status or the exception class
name only (never a body, never the token); the service logs them.
"""

from __future__ import annotations

import shutil
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx

from core.config import settings
from core.passkey_vault import PasskeyVaultCode, PasskeyVaultError
from core.secure_paths import make_private_dir, make_private_file

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["KEEP", "newest_vault_snapshot_at", "snapshot_enabled", "take_vault_snapshot"]

KEEP = 7
_REJECTED: PasskeyVaultCode = "passkey_vault_rejected"
_UNREACHABLE: PasskeyVaultCode = "passkey_vault_unreachable"
_STEM = "openbao"
_SUFFIX = ".snap"
_STAMP = "%Y%m%dT%H%M%S%fZ"
_MIN_TIMEOUT = 60.0
_CHUNK = 1 << 20


def snapshot_enabled() -> bool:
    return bool(settings.passkey_vault.snapshot_token)


def newest_vault_snapshot_at(*, now: datetime | None = None) -> datetime | None:
    """When the newest local snapshot was taken, from its name; future-dated names ignored."""
    folder = settings.db.backup_dir
    if not folder.is_dir():
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


def _prune(folder: Path, written: Path) -> None:
    others = sorted(path for path in folder.glob(f"{_STEM}-*{_SUFFIX}") if path != written)
    for stale in others[: max(0, len(others) - (KEEP - 1))]:
        with suppress(OSError):
            stale.unlink(missing_ok=True)


def _mirror(snapshot: Path) -> None:
    mirror = settings.session_vault.mirror_dir
    if mirror is None:
        return
    folder = mirror / _STEM
    make_private_dir(folder)
    partial = folder / f"{snapshot.name}.part"
    shutil.copyfile(snapshot, partial)
    partial.replace(folder / snapshot.name)
    make_private_file(folder / snapshot.name)
    _prune(folder, folder / snapshot.name)


async def _download(partial: Path) -> None:
    config = settings.passkey_vault
    url = f"{config.addr.rstrip('/')}/v1/sys/storage/raft/snapshot"
    async with (
        httpx.AsyncClient(timeout=max(config.timeout_seconds, _MIN_TIMEOUT)) as client,
        client.stream("GET", url, headers={"X-Vault-Token": config.snapshot_token}) as response,
    ):
        if not response.is_success:
            raise PasskeyVaultError(_REJECTED, f"http_{response.status_code}")
        with partial.open("wb") as out:
            async for chunk in response.aiter_bytes(_CHUNK):
                out.write(chunk)


async def take_vault_snapshot(*, now: datetime | None = None) -> Path:
    """Fetch, publish, prune and mirror a snapshot; its path. Raises ``PasskeyVaultError``."""
    folder = settings.db.backup_dir
    make_private_dir(folder)
    stamp = (now or datetime.now(UTC)).strftime(_STAMP)
    target = folder / f"{_STEM}-{stamp}{_SUFFIX}"
    partial = folder / f"{target.name}.part"
    try:
        await _download(partial)
        empty = partial.stat().st_size == 0
    except (httpx.HTTPError, httpx.InvalidURL, OSError) as exc:
        partial.unlink(missing_ok=True)
        # ``from None``: an httpx error can quote the request's headers, the token among them.
        raise PasskeyVaultError(_UNREACHABLE, type(exc).__name__) from None
    except PasskeyVaultError:
        partial.unlink(missing_ok=True)
        raise
    if empty:
        partial.unlink(missing_ok=True)
        raise PasskeyVaultError(_REJECTED, "empty_snapshot")
    partial.replace(target)
    make_private_file(target)
    _prune(folder, target)
    try:
        _mirror(target)
    except OSError as exc:  # the local snapshot stands; the next one retries the mirror.
        raise PasskeyVaultError(_UNREACHABLE, f"mirror_{type(exc).__name__}") from exc
    return target
