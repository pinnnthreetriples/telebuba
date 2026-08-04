"""Owner-only filesystem modes for the paths that hold Telegram credentials.

``.session`` files and the tdata they are converted from ARE the credentials —
non-negotiable #7 keeps them out of logs and git, and the same reasoning applies
to the file mode: a default-umask ``mkdir`` leaves ``sessions/`` at 0755 and every
local account able to read the fleet's logins.

POSIX-only by construction. On Windows ``os.chmod`` only toggles the read-only bit,
so applying it there would be a no-op that misreports what happened; the guard
below makes that explicit instead of pretending. Telebuba deploys on Linux, where
this is real, and Windows is the development checkout, where it is inert.

A failing ``chmod`` never blocks the write it protects: an import must still
complete on a filesystem that has no POSIX modes to set (a mounted share, a
container volume with ``no_perm``). ``suppress`` is the same call the original
inline ``chmod`` in ``services.accounts._uploads`` used, kept deliberately.
"""

from __future__ import annotations

import os
from contextlib import suppress
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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


__all__ = ["make_private_dir", "make_private_file"]
