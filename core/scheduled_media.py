"""Content-addressed file store for media waiting for its scheduled publish time.

A file is named ``<sha256><suffix>``, so the same photo scheduled for fifty
accounts is stored once. Nothing here knows about posts: the service passes the
set of names its rows still reference into :func:`collect_garbage`.

Ordering is the whole safety argument, and it is the caller's to keep: every
write, every "is it there + insert the row that references it", and every sweep
run under :func:`store_lock`. A sweep also spares any file younger than its grace,
which covers the window between an upload and the schedule call that uses it
(re-uploading an existing file refreshes its mtime for the same reason).

Windows: replacing or unlinking a file another handle has open raises, so an
existing target is never replaced and a failed unlink is simply retried by the
next sweep.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import re
import shutil
import time
import uuid
from contextlib import suppress
from typing import TYPE_CHECKING, NamedTuple

from PIL import Image

from core.config import settings
from core.secure_paths import make_private_dir, make_private_file

if TYPE_CHECKING:
    from pathlib import Path

IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})
VIDEO_SUFFIXES = frozenset({".mp4", ".mov"})
_NAME_RE = re.compile(r"^[0-9a-f]{64}\.(?:jpg|jpeg|png|webp|mp4|mov)$")
_TMP_PREFIX = ".tmp_"
_CHUNK_BYTES = 1024 * 1024
_THUMB_QUALITY = 80
# Stable codes; ``schemas.scheduled_posts.ScheduledPostRefusalCode`` members.
_MISSING = "scheduled_media_missing"
_INVALID = "scheduled_media_invalid"

_state: dict[str, asyncio.Lock] = {}


class MediaStoreError(Exception):
    """A stored file is missing, corrupt or not decodable; ``code`` is stable."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class StoredMedia(NamedTuple):
    name: str
    size: int
    # False when the same content was already stored (it only got a fresh mtime).
    created: bool


def store_lock() -> asyncio.Lock:
    """The one lock every write, reference check and sweep runs under."""
    lock = _state.get("lock")
    if lock is None:
        lock = _state["lock"] = asyncio.Lock()
    return lock


def reset_for_tests() -> None:
    """Drop the lock, which binds to the event loop that first awaited it."""
    _state.clear()


def is_media_name(name: str) -> bool:
    return _NAME_RE.fullmatch(name) is not None


def is_video(name: str) -> bool:
    return any(name.endswith(suffix) for suffix in VIDEO_SUFFIXES)


def _store_dir() -> Path:
    path = settings.scheduled_posts.media_dir
    # A symlink would let writes and the sweep escape the intended directory.
    if path.is_symlink():
        msg = "Scheduled media directory must not be a symbolic link"
        raise RuntimeError(msg)
    make_private_dir(path)
    return path


def _path_of(name: str) -> Path:
    # The lexical guard is authoritative: only a well-formed name ever becomes a path.
    if not is_media_name(name):
        raise MediaStoreError(_MISSING)
    return _store_dir() / name


def _digest(path: Path) -> tuple[str, int]:
    sha = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(_CHUNK_BYTES):
            sha.update(chunk)
            size += len(chunk)
    return sha.hexdigest(), size


def _ingest(source: Path, suffix: str) -> StoredMedia:
    digest, size = _digest(source)
    name = f"{digest}{suffix}"
    directory = _store_dir()
    target = directory / name
    if target.exists():
        # Same content already stored: keep it, and restart its sweep grace.
        os.utime(target, None)
        return StoredMedia(name, size, created=False)
    temporary = directory / f"{_TMP_PREFIX}{uuid.uuid4().hex}{suffix}"
    try:
        shutil.copyfile(source, temporary)
        with temporary.open("rb+") as handle:
            os.fsync(handle.fileno())
        make_private_file(temporary)
        temporary.replace(target)
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
    return StoredMedia(name, size, created=True)


async def ingest(source: Path, suffix: str) -> StoredMedia:
    """Store a copy of ``source``; the caller holds :func:`store_lock`."""
    return await asyncio.to_thread(_ingest, source, suffix)


def _discard(name: str) -> None:
    with suppress(OSError):
        _path_of(name).unlink(missing_ok=True)


async def discard(name: str) -> None:
    """Drop a file this request just stored and then refused. Under the lock."""
    await asyncio.to_thread(_discard, name)


def _verify_image(source: Path) -> None:
    try:
        with Image.open(source) as image:
            image.verify()
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        raise MediaStoreError(_INVALID) from exc


async def verify_image(source: Path) -> None:
    """Refuse a file Pillow cannot decode, at upload time rather than publish time."""
    await asyncio.to_thread(_verify_image, source)


def _read(name: str) -> bytes:
    path = _path_of(name)
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise MediaStoreError(_MISSING) from exc
    # A restored backup or a truncated write must not publish the wrong bytes.
    if hashlib.sha256(content).hexdigest() != name.split(".", 1)[0]:
        raise MediaStoreError(_MISSING)
    return content


async def read_media(name: str) -> bytes:
    return await asyncio.to_thread(_read, name)


def _exists(name: str) -> bool:
    if not is_media_name(name):
        return False
    path = _store_dir() / name
    if not path.is_file():
        return False
    os.utime(path, None)
    return True


async def touch_existing(name: str) -> bool:
    """True when ``name`` is stored; also restarts its sweep grace. Under the lock."""
    return await asyncio.to_thread(_exists, name)


def _size_of(name: str) -> int | None:
    try:
        return _path_of(name).stat().st_size
    except (OSError, MediaStoreError):
        return None


async def size_of(name: str) -> int | None:
    """Bytes of a stored file, or ``None`` when it is not stored."""
    return await asyncio.to_thread(_size_of, name)


def _thumbnail(name: str, max_px: int) -> bytes | None:
    if is_video(name):
        return None
    try:
        with Image.open(_path_of(name)) as image:
            image.thumbnail((max_px, max_px))
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, format="JPEG", quality=_THUMB_QUALITY)
    except (OSError, ValueError, MediaStoreError, Image.DecompressionBombError):
        return None
    return buffer.getvalue()


async def thumbnail(name: str) -> bytes | None:
    """A small JPEG preview of a stored image, or ``None`` (video, gone, broken)."""
    return await asyncio.to_thread(_thumbnail, name, settings.scheduled_posts.thumb_max_px)


def _total_bytes() -> int:
    total = 0
    for path in _store_dir().iterdir():
        with suppress(OSError):
            total += path.stat().st_size
    return total


async def total_bytes() -> int:
    return await asyncio.to_thread(_total_bytes)


def _collect(referenced: frozenset[str], grace_seconds: int, now: float) -> int:
    removed = 0
    cutoff = now - grace_seconds
    for path in _store_dir().iterdir():
        is_temporary = path.name.startswith(_TMP_PREFIX)
        if not is_temporary and (not is_media_name(path.name) or path.name in referenced):
            continue
        try:
            if path.lstat().st_mtime > cutoff:
                continue
            path.unlink()
        except OSError:
            continue  # open elsewhere (Windows) or already gone: the next sweep retries
        removed += 1
    return removed


async def collect_garbage(referenced: frozenset[str], grace_seconds: int) -> int:
    """Delete stored files no post references any more. Under the lock."""
    return await asyncio.to_thread(_collect, referenced, grace_seconds, time.time())


__all__ = [
    "IMAGE_SUFFIXES",
    "VIDEO_SUFFIXES",
    "MediaStoreError",
    "StoredMedia",
    "collect_garbage",
    "discard",
    "ingest",
    "is_media_name",
    "is_video",
    "read_media",
    "reset_for_tests",
    "size_of",
    "store_lock",
    "thumbnail",
    "total_bytes",
    "touch_existing",
    "verify_image",
]
