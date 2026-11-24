"""A tombstone inside a browser profile that a deleted account could not take with it.

The profile holds a signed-in Telegram Web session. Deleting an account removes it, but a
removal can fail (a locked file, a Chrome helper still alive) and nothing retries. So the
deletion first writes :data:`TOMBSTONE_NAME` inside the profile; the startup sweep removes
exactly the profiles that carry one. Never "an account of this profile is missing from the
database": a wrong, empty or restored-from-backup database would then match every profile.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from core.web_login.browser import web_profiles_root

if TYPE_CHECKING:
    from pathlib import Path

TOMBSTONE_NAME = ".telebuba-remove"


def mark_profile_for_removal(profile_dir: Path) -> bool:
    """Write the tombstone into an existing profile; False when there is no such directory.

    Raises ``OSError`` when the file cannot be written.
    """
    if not profile_dir.is_dir():
        return False
    (profile_dir / TOMBSTONE_NAME).write_text("removal pending", encoding="utf-8")
    return True


def clear_removal_mark(profile_dir: Path) -> None:
    """Drop a stale tombstone: the account exists (it is opening a window), so nothing is pending.

    An account deleted and added again under the same id reuses the directory; a tombstone
    left by a removal that failed would otherwise take its new signed-in profile at the next
    startup. A mark that cannot be dropped raises ``OSError``: opening over it is refused.
    """
    (profile_dir / TOMBSTONE_NAME).unlink(missing_ok=True)


def _marked_profiles(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    marked: list[Path] = []
    for child in root.iterdir():
        # Real directories directly under the root: never a link out of it, never the root.
        if child.is_symlink() or not child.is_dir() or child.resolve().parent != root.resolve():
            continue
        if (child / TOMBSTONE_NAME).is_file():
            marked.append(child)
    return marked


async def list_marked_profiles() -> list[Path]:
    """Profile directories under the profiles root that hold a tombstone."""
    return await asyncio.to_thread(_marked_profiles, web_profiles_root())
