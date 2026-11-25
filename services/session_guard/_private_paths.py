"""Start-up look at who else on this computer can reach the credential folders.

``.session`` files, the database (cloud passwords), the vault and its copies are only as
private as their folders: on Windows a folder inherits its parent's access list, and a data
disk's root grants "Authenticated Users" access. Each path that is open is journalled once
(``private_path_open``, by stable name, never by path); nothing is changed here — narrowing
a live credential folder is the operator's command (``python -m tools.secure_paths fix``).
A check that fails journals ``private_path_check_failed`` and never blocks start-up.
"""

from __future__ import annotations

import asyncio

from core.logging import log_event
from core.private_paths import configured_private_paths
from core.secure_paths import open_private_paths

__all__ = ["check_private_paths"]


class _OpenPaths(dict[str, list[str]]):
    """The open paths (name to grants); ``failed`` maps each unreadable one to its error type."""

    def __init__(self) -> None:
        super().__init__()
        self.failed: dict[str, str] = {}


def _open_by_name() -> _OpenPaths:
    opened = _OpenPaths()
    for name, path in configured_private_paths().items():
        try:
            for _path, grants in open_private_paths([path]):
                opened[name] = grants
        except Exception as exc:  # noqa: BLE001 - one unreadable path must not hide the rest
            opened.failed[name] = type(exc).__name__
    return opened


async def check_private_paths() -> None:
    """Journal every configured path other users can reach. Never raises."""
    try:
        opened = await asyncio.to_thread(_open_by_name)
    except Exception as exc:  # noqa: BLE001 - a failed look must not stop start-up
        await log_event(
            "WARNING", "private_path_check_failed", extra={"error_type": type(exc).__name__}
        )
        return
    for name, error_type in getattr(opened, "failed", {}).items():
        await log_event(
            "WARNING",
            "private_path_check_failed",
            extra={"what": name, "error_type": error_type},
        )
    for name, grants in opened.items():
        await log_event("WARNING", "private_path_open", extra={"what": name, "grants": grants})
