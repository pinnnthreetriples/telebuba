"""Content-addressed store for photos attached to chat-broadcast messages.

A file is named ``<sha256><suffix>``: the same photo in many campaigns is stored once,
and a name that does not match its bytes on read (a restored backup, a truncated
write) is refused rather than sent. Photos are profile-photo sized, so they travel as
bytes; there is no sweep — a campaign's settings are what reference a file and
removing one leaves at most a few megabytes behind.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import re
import uuid
from contextlib import suppress
from typing import TYPE_CHECKING

from PIL import Image

from core.config import settings
from core.secure_paths import make_private_dir, make_private_file

if TYPE_CHECKING:
    from pathlib import Path

PHOTO_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})
_NAME_RE = re.compile(r"^[0-9a-f]{64}\.(?:jpg|jpeg|png|webp)$")
_TMP_PREFIX = ".tmp_"
_NOT_FOUND = "media_not_found"
_INVALID = "media_invalid"


class BroadcastMediaError(Exception):
    """``code`` is a stable ``ChatBroadcastRefusalCode`` member."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _store_dir() -> Path:
    path = settings.chat_broadcast.media_dir
    if path.is_symlink():
        msg = "Chat broadcast media directory must not be a symbolic link"
        raise RuntimeError(msg)
    make_private_dir(path)
    return path


def _path_of(media_id: str) -> Path:
    if _NAME_RE.fullmatch(media_id) is None:
        raise BroadcastMediaError(_NOT_FOUND)
    return _store_dir() / media_id


def _store(content: bytes, suffix: str) -> str:
    try:
        with Image.open(io.BytesIO(content)) as image:
            image.verify()
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        raise BroadcastMediaError(_INVALID) from exc
    media_id = f"{hashlib.sha256(content).hexdigest()}{suffix}"
    target = _path_of(media_id)
    if target.exists():
        return media_id
    temporary = target.with_name(f"{_TMP_PREFIX}{uuid.uuid4().hex}{suffix}")
    try:
        temporary.write_bytes(content)
        make_private_file(temporary)
        temporary.replace(target)
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
    return media_id


async def store_photo(content: bytes, suffix: str) -> str:
    """Store a decodable image; returns its media id. ``suffix`` is pre-validated."""
    if suffix not in PHOTO_SUFFIXES:
        raise BroadcastMediaError(_INVALID)
    return await asyncio.to_thread(_store, content, suffix)


def _read(media_id: str) -> bytes:
    path = _path_of(media_id)
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise BroadcastMediaError(_NOT_FOUND) from exc
    if hashlib.sha256(content).hexdigest() != media_id.split(".", 1)[0]:
        raise BroadcastMediaError(_NOT_FOUND)
    return content


async def read_photo(media_id: str) -> bytes:
    return await asyncio.to_thread(_read, media_id)
