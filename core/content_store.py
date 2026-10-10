"""Content-addressed media store shared by the campaign features.

A file is named ``<sha256><suffix>``: the same file in many campaigns is stored once,
and a name that does not match its bytes on read (a restored backup, a truncated write)
is refused rather than sent. Images must decode; a video must at least carry the MP4/MOV
``ftyp`` box — the publisher's ffmpeg pass is what proves the rest. Files travel as
bytes; there is no sweep — a campaign's settings are what reference a file, and a removed
reference leaves the file behind.

Each feature keeps its own directory and suffix set (``core.chat_broadcast_media``,
``core.masstagging_media``); the codes are stable refusal codes of both features.
"""

from __future__ import annotations

import hashlib
import io
import re
import uuid
from contextlib import suppress
from typing import TYPE_CHECKING

from PIL import Image

from core.secure_paths import make_private_dir, make_private_file

if TYPE_CHECKING:
    from pathlib import Path

IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})
VIDEO_SUFFIXES = frozenset({".mp4", ".mov"})
_NAME_RE = re.compile(r"^[0-9a-f]{64}(\.[a-z0-9]{2,5})$")
_TMP_PREFIX = ".tmp_"
# The ISO media box every MP4/MOV starts with sits within its first 12 bytes.
_VIDEO_MAGIC = b"ftyp"
_VIDEO_HEAD = 16
NOT_FOUND = "media_not_found"
INVALID = "media_invalid"


class ContentStoreError(Exception):
    """``code`` is a stable refusal code: ``media_not_found`` or ``media_invalid``."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _store_dir(directory: Path) -> Path:
    if directory.is_symlink():
        msg = "Media directory must not be a symbolic link"
        raise RuntimeError(msg)
    make_private_dir(directory)
    return directory


def _path_of(directory: Path, media_id: str, suffixes: frozenset[str]) -> Path:
    match = _NAME_RE.fullmatch(media_id)
    if match is None or match.group(1) not in suffixes:
        raise ContentStoreError(NOT_FOUND)
    return _store_dir(directory) / media_id


def _verify(content: bytes, suffix: str) -> None:
    if suffix in VIDEO_SUFFIXES:
        if _VIDEO_MAGIC not in content[:_VIDEO_HEAD]:
            raise ContentStoreError(INVALID)
        return
    try:
        with Image.open(io.BytesIO(content)) as image:
            image.verify()
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        raise ContentStoreError(INVALID) from exc


def store(content: bytes, suffix: str, directory: Path, suffixes: frozenset[str]) -> str:
    """Store verified bytes; returns the media id. Blocking — call through a thread."""
    if suffix not in suffixes:
        raise ContentStoreError(INVALID)
    _verify(content, suffix)
    media_id = f"{hashlib.sha256(content).hexdigest()}{suffix}"
    target = _path_of(directory, media_id, suffixes)
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


def read(media_id: str, directory: Path, suffixes: frozenset[str]) -> bytes:
    """The stored bytes, checked against their name. Blocking — call through a thread."""
    path = _path_of(directory, media_id, suffixes)
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise ContentStoreError(NOT_FOUND) from exc
    if hashlib.sha256(content).hexdigest() != media_id.split(".", 1)[0]:
        raise ContentStoreError(NOT_FOUND)
    return content
