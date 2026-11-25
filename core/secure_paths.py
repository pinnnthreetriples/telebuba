"""Owner-only filesystem modes for the paths that hold Telegram credentials.

``.session`` files and the tdata they are converted from ARE the credentials —
non-negotiable #7 keeps them out of logs and git, and the same reasoning applies
to the file mode: a default-umask ``mkdir`` leaves ``sessions/`` at 0755 and every
local account able to read the fleet's logins.

The modes are POSIX-only: on Windows ``os.chmod`` only toggles the read-only bit, so the
helpers below do not pretend to apply them there. Windows is a real deployment, though, and
its protection is the NTFS access list, which a new folder inherits from its parent (a data
disk's root grants "Authenticated Users" access). That is only CHECKED automatically
(:func:`open_private_paths`, at start-up); narrowing a live credential folder is done by the
operator's command (``python -m tools.secure_paths fix``, :func:`restrict_private_path`).

A failing ``chmod`` never blocks the write it protects: an import must still
complete on a filesystem that has no POSIX modes to set (a mounted share, a
container volume with ``no_perm``). ``suppress`` is the same call the original
inline ``chmod`` in ``services.accounts._uploads`` used, kept deliberately.
"""

from __future__ import annotations

import os
import stat
from contextlib import suppress
from typing import TYPE_CHECKING

from core import _windows_acl

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

_DIR_MODE = 0o700
_FILE_MODE = 0o600
_IS_POSIX = os.name == "posix"


def _chmod(path: Path, mode: int) -> None:
    if not _IS_POSIX:
        return
    with suppress(OSError):
        path.chmod(mode)


def make_private_dir(path: Path) -> None:
    """``mkdir -p`` ``path`` and leave it owner-only (0700).

    The ``chmod`` runs unconditionally rather than via ``mkdir(mode=...)``: that
    argument is masked by the umask, is ignored for a directory that already
    exists, and is not applied to the parents ``parents=True`` creates.
    """
    path.mkdir(parents=True, exist_ok=True)
    _chmod(path, _DIR_MODE)


def make_private_file(path: Path) -> None:
    """Leave an existing file owner-only (0600)."""
    _chmod(path, _FILE_MODE)


def _posix_grants(path: Path) -> list[str]:
    return ["group_or_other"] if stat.S_IMODE(path.stat().st_mode) & 0o077 else []


def open_private_paths(paths: Iterable[Path]) -> list[tuple[Path, list[str]]]:
    """The existing ``paths`` that other users of this computer can reach, with who.

    POSIX: a mode that gives group or other anything is ``["group_or_other"]``. Windows: the
    broad principals of the NTFS access list (``everyone``, ``authenticated_users``, ...).
    A path that does not exist is skipped; one that cannot be read raises.
    """
    found: list[tuple[Path, list[str]]] = []
    for path in paths:
        if not path.exists():
            continue
        grants = _windows_acl.broad_grants(path) if os.name == "nt" else _posix_grants(path)
        if grants:
            found.append((path, grants))
    return found


def restrict_private_path(path: Path) -> None:
    """Make ``path`` reachable by its owner only (Windows: owner, SYSTEM, Administrators).

    Only for the operator's explicit command; raises on failure, unlike the quiet helpers
    above.
    """
    if os.name == "nt":
        _windows_acl.restrict_to_owner(path)
    else:
        path.chmod(_DIR_MODE if path.is_dir() else _FILE_MODE)


__all__ = [
    "make_private_dir",
    "make_private_file",
    "open_private_paths",
    "restrict_private_path",
]
