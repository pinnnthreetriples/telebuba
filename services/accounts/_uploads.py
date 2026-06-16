"""Upload validation + session-file persistence for the accounts service."""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path

_PROFILE_PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
_STORY_IMAGE_SUFFIXES = _PROFILE_PHOTO_SUFFIXES
_STORY_VIDEO_SUFFIXES = {".mp4", ".mov"}
_PROFILE_MUSIC_SUFFIXES = {".mp3", ".m4a"}


def _session_filename(filename: str) -> str:
    name = Path(filename).name
    if Path(name).suffix.lower() != ".session":
        msg = "Upload a .session file"
        raise ValueError(msg)
    if not Path(name).stem:
        msg = "Session file name is empty"
        raise ValueError(msg)
    return name


def _write_session_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    # Best-effort chmod 0600. ``os.chmod`` on Windows only affects the read-only
    # bit, so the protection is mainly relevant on POSIX. We swallow OSError to
    # keep imports working on systems where chmod is unavailable.
    with suppress(OSError):
        path.chmod(0o600)


def _validate_upload(
    *,
    filename: str,
    content: bytes,
    max_bytes: int,
    allowed_suffixes: set[str],
    label: str,
) -> None:
    if not content:
        msg = f"{label} file is empty"
        raise ValueError(msg)
    if len(content) > max_bytes:
        msg = f"{label} file is too large"
        raise ValueError(msg)
    suffix = Path(filename).suffix.lower()
    if suffix not in allowed_suffixes:
        allowed = ", ".join(sorted(allowed_suffixes))
        msg = f"{label} must be one of: {allowed}"
        raise ValueError(msg)
