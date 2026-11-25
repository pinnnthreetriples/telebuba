"""Helpers for the tests that open or close a temp folder to the other users of the computer."""

from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def open_to_others(path: Path) -> None:
    """Give every other local user read access to ``path`` (a mode on POSIX, an ACE on NTFS)."""
    if sys.platform == "win32":
        right = "(OI)(CI)R" if path.is_dir() else "R"
        subprocess.run(  # noqa: S603 - a fixed tool and a temp path, no shell
            ["icacls", str(path), "/grant", f"*S-1-5-11:{right}"],  # noqa: S607
            check=True,
            capture_output=True,
        )
    else:
        path.chmod(0o755 if path.is_dir() else 0o644)
