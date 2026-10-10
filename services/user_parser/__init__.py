"""User parser — collect people from groups and channels into saved bases.

Package facade: re-exports only. ``runs`` starts, follows and stops a run; ``bases`` serves
what runs saved, their export and the presets.
"""

from __future__ import annotations

from services.user_parser._pool import list_reading_accounts
from services.user_parser.bases import (
    BaseRunningError,
    PresetNameTakenError,
    create_preset,
    delete_base,
    delete_preset,
    export_base,
    list_base_users,
    list_bases,
    list_presets,
    rename_base,
)
from services.user_parser.runs import (
    UserParserInvalidError,
    get_run,
    reconcile_user_parser_on_startup,
    shutdown_user_parser_on_shutdown,
    start_run,
    stop_run,
)

__all__ = [
    "BaseRunningError",
    "PresetNameTakenError",
    "UserParserInvalidError",
    "create_preset",
    "delete_base",
    "delete_preset",
    "export_base",
    "get_run",
    "list_base_users",
    "list_bases",
    "list_presets",
    "list_reading_accounts",
    "reconcile_user_parser_on_startup",
    "rename_base",
    "shutdown_user_parser_on_shutdown",
    "start_run",
    "stop_run",
]
