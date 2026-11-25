"""The configured paths that hold credentials, under the stable names the journal uses.

One map for the start-up check (``services.session_guard._private_paths``) and the operator's
command (``tools.secure_paths``). The names are what events carry instead of a filesystem path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.config import settings
from core.web_login.browser import web_profiles_root

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["configured_private_paths"]


def configured_private_paths() -> dict[str, Path]:
    """Stable name -> path for every place the app keeps keys, passwords or copies of them.

    The vault mirror (usually on another disk) also holds the database copies.
    """
    paths = {
        "sessions": settings.telegram.session_dir,
        "quarantine": settings.session_vault.quarantine_dir,
        "database": settings.db.path,
        "backups": settings.db.backup_dir,
        "web_profiles": web_profiles_root(),
        "session_vault": settings.session_vault.dir,
    }
    if settings.session_vault.mirror_dir is not None:
        paths["vault_mirror"] = settings.session_vault.mirror_dir
    return paths
