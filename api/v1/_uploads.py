"""Pre-read multipart size guard shared by the accounts upload routes.

Rejecting an over-cap upload *before* ``await file.read()`` prevents the real RAM
blow-up: ``file.read()`` materializing the whole part as a single ``bytes`` object.
It does NOT prevent the transfer itself — Starlette's multipart parser has already
received the body and spooled it (``SpooledTemporaryFile``: ~1 MB in RAM, then
disk) by the time the handler runs, and ``.size`` is the final measured byte count.
So this guard caps peak memory, not bandwidth/disk (a Content-Length middleware
would be needed for that). When ``.size`` is unavailable (``None``) we skip and let
the service-layer size check reject after the read — kept as defense-in-depth.

The module also owns the staging directory those uploads stream into. What "private"
means there is platform-dependent, and this is the whole of it: on POSIX the directory
is chmod 0700 and its mode is re-checked before a single byte is written, so a wrong
mode fails the request loudly. On Windows ``chmod`` reaches nothing but the read-only
bit, so neither the set nor the check happens and staged credentials inherit whatever
the parent directory grants — there, the bounded lifetime (removal on every exit path,
plus the startup TTL sweep) is the only protection, not the file mode.
"""

from __future__ import annotations

import asyncio
import logging
import os
import stat
import tempfile
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import HTTPException
from fastapi import status as http_status

from core.config import settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from fastapi import UploadFile

_STREAM_CHUNK_BYTES = 1024 * 1024
_STAGING_PREFIX = "telebuba_upload_"
_CLEANUP_ATTEMPTS = 3
_CLEANUP_RETRY_SECONDS = 0.05

logger = logging.getLogger(__name__)


def _make_private_dir(path: Path) -> None:
    """Create the staging directory, owner-only (0700) where the OS has such a mode.

    Deliberately a copy of ``core.secure_paths.make_private_dir`` rather than a call
    to it: ``api/`` may import from ``core`` only ``config`` and ``logging``, and
    ``tests/test_architecture.py`` enforces that. It keeps the same POSIX-only
    reasoning — on Windows ``chmod`` toggles nothing but the read-only bit, so
    applying it there would report a privacy guarantee that was never made.
    """
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        path.chmod(0o700)


def reject_oversized_upload(file: UploadFile, *, max_bytes: int, detail: str) -> None:
    """Raise 400 with ``detail`` if the multipart part is known to exceed ``max_bytes``."""
    if file.size is not None and file.size > max_bytes:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=detail)


def _staging_dir() -> Path:
    path = settings.api.upload_staging_dir
    # A configured symlink would let staging and startup cleanup escape the
    # intended owner-only directory. Configuration is trusted, but fail loud on
    # this easy-to-make deployment mistake.
    if path.is_symlink():
        msg = "Upload staging directory must not be a symbolic link"
        raise RuntimeError(msg)
    try:
        _make_private_dir(path)
    except OSError:
        msg = "Upload staging directory permissions could not be set"
        raise RuntimeError(msg) from None
    if os.name == "posix":
        # Nothing equivalent runs on Windows: there is no mode to read back that
        # would mean anything. Do not read this absence as a verified directory.
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except OSError:
            msg = "Upload staging directory permissions could not be verified"
            raise RuntimeError(msg) from None
        if mode & 0o077:
            msg = "Upload staging directory must be owner-only"
            raise RuntimeError(msg)
    return path


async def _file_io[T](
    function: Callable[..., T],
    *args: object,
    **kwargs: object,
) -> T:
    """Wait for submitted file I/O to really finish before propagating cancellation."""
    work = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        # A thread cannot be cancelled. Do not close/unlink the file underneath it.
        with suppress(Exception):
            await work
        raise


async def _remove_with_retry(path: Path) -> bool:
    """Best-effort credential deletion, with bounded retry and path-free logging."""
    for attempt in range(_CLEANUP_ATTEMPTS):
        try:
            await _file_io(path.unlink, missing_ok=True)
        except OSError as exc:
            if attempt + 1 < _CLEANUP_ATTEMPTS:
                await asyncio.sleep(_CLEANUP_RETRY_SECONDS)
                continue
            # Never put the absolute credential path into logs. Startup cleanup
            # retries leftovers after abrupt shutdown or a transient filesystem
            # failure, and on POSIX the 0700 directory limits exposure until then.
            # ``logger.exception`` would stringify ``OSError(filename=...)`` and
            # reintroduce the absolute credential path this boundary removes.
            logger.error(  # noqa: TRY400
                "upload staging cleanup failed (%s)",
                type(exc).__name__,
            )
        else:
            return True
    return False


def _stale_staging_files(directory: Path, now: float) -> list[Path]:
    cutoff = now - settings.api.upload_staging_ttl_seconds
    stale: list[Path] = []
    for path in directory.iterdir():
        if not path.name.startswith(_STAGING_PREFIX):
            continue
        try:
            modified = path.lstat().st_mtime
        except OSError:
            continue
        if modified <= cutoff:
            stale.append(path)
    return stale


async def cleanup_stale_uploads() -> None:
    """Remove credential staging files abandoned by a prior process lifetime."""
    directory = await _file_io(_staging_dir)
    stale = await _file_io(_stale_staging_files, directory, time.time())
    for path in stale:
        await _remove_with_retry(path)


@asynccontextmanager
async def staged_upload(
    file: UploadFile,
    *,
    max_bytes: int,
    detail: str,
    suffix: str = "",
) -> AsyncIterator[Path]:
    """Stream an UploadFile into the staging directory, removing it on every exit path."""
    reject_oversized_upload(file, max_bytes=max_bytes, detail=detail)
    directory = await _file_io(_staging_dir)
    descriptor, raw_path = tempfile.mkstemp(
        prefix=_STAGING_PREFIX,
        suffix=suffix,
        dir=directory,
    )
    path = Path(raw_path)
    target = os.fdopen(descriptor, "wb")
    total = 0
    try:
        while chunk := await file.read(_STREAM_CHUNK_BYTES):
            total += len(chunk)
            if total > max_bytes:
                raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=detail)
            await _file_io(target.write, chunk)
        await _file_io(target.close)
        yield path
    finally:
        # Cancellation cannot close a file while a thread is writing it. Wait for
        # the close itself, then retry unlink independently so a transient error
        # does not silently strand credential material.
        with suppress(OSError):
            await _file_io(target.close)
        await _remove_with_retry(path)


__all__ = ["cleanup_stale_uploads", "reject_oversized_upload", "staged_upload"]
