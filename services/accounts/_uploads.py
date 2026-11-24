"""Upload validation + session-file persistence for the accounts service."""

from __future__ import annotations

from pathlib import Path

from core.secure_paths import make_private_dir, make_private_file
from core.session_file import inspect_session_file
from services.accounts._result import AccountActionError

_PROFILE_PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
_STORY_IMAGE_SUFFIXES = _PROFILE_PHOTO_SUFFIXES
_STORY_VIDEO_SUFFIXES = {".mp4", ".mov"}
_PROFILE_MUSIC_SUFFIXES = {".mp3", ".m4a"}
# The name an upload has until it is proven a session: it does not end in ``.session``, so
# nothing (Telethon, the orphan listing, the vault) ever takes it for an account's file.
STAGING_SUFFIX = ".importing"
SESSION_FILE_INVALID = "session_file_invalid"


def _session_filename(filename: str) -> str:
    name = Path(filename).name
    if Path(name).suffix.lower() != ".session":
        msg = "Upload a .session file"
        raise ValueError(msg)
    # Phone-named sessions (``+522491104289.session``) are the common case, and
    # ``+`` is outside the account_id charset; the digits alone identify them.
    stem = Path(name).stem.lstrip("+")
    if not stem:
        msg = "Session file name is empty"
        raise ValueError(msg)
    return stem + Path(name).suffix


def _require_session(staged: Path) -> None:
    if inspect_session_file(staged) != "ok":
        raise AccountActionError(SESSION_FILE_INVALID)


def _write_session_file(path: Path, content: bytes) -> None:
    """Land an uploaded session at ``path``: written aside, proven a session, then renamed.

    The final name never holds an unvalidated or half-written file, so a crash leaves at most
    the staging name, which the next import of the same name clears. Anything that is not a
    Telethon session holding a key is the refusal ``session_file_invalid``. The caller holds
    the import lock and has checked that ``path`` is free.
    """
    # Directory first: a 0600 file inside a 0755 directory still leaks its name and
    # size to every local account. See ``core.secure_paths`` for the POSIX caveat.
    make_private_dir(path.parent)
    staging = path.with_name(path.name + STAGING_SUFFIX)
    staging.unlink(missing_ok=True)
    try:
        staging.write_bytes(content)
        make_private_file(staging)
        _require_session(staging)
        staging.replace(path)
        make_private_file(path)
    except BaseException:
        staging.unlink(missing_ok=True)
        raise


def _validate_content(
    *,
    content: bytes,
    max_bytes: int,
    label: str,
) -> None:
    """Content-only checks (empty / size cap) for uploads without a filename."""
    if not content:
        msg = f"{label} file is empty"
        raise ValueError(msg)
    if len(content) > max_bytes:
        msg = f"{label} file is too large"
        raise ValueError(msg)


def _validate_upload(
    *,
    filename: str,
    content: bytes,
    max_bytes: int,
    allowed_suffixes: set[str],
    label: str,
) -> None:
    _validate_content(content=content, max_bytes=max_bytes, label=label)
    suffix = Path(filename).suffix.lower()
    if suffix not in allowed_suffixes:
        allowed = ", ".join(sorted(allowed_suffixes))
        msg = f"{label} must be one of: {allowed}"
        raise ValueError(msg)
