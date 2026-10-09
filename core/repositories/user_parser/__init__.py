"""Data-access repository for the user parser.

The public surface; importing it registers the tables in ``core.db._metadata`` (via
``_tables``). Public functions wrap sync helpers via ``asyncio.to_thread`` and return
Pydantic models — never raw rows.
"""

from __future__ import annotations

from core.repositories.user_parser._presets import (
    PresetMiss,
    create_preset,
    delete_preset,
    list_presets,
)
from core.repositories.user_parser._runs import (
    DeleteMiss,
    create_run,
    delete_base,
    fetch_run,
    interrupt_running,
    list_bases,
    rename_base,
    settle_run,
)
from core.repositories.user_parser._users import collected_user_ids, page_users, users_after

__all__ = [
    "DeleteMiss",
    "PresetMiss",
    "collected_user_ids",
    "create_preset",
    "create_run",
    "delete_base",
    "delete_preset",
    "fetch_run",
    "interrupt_running",
    "list_bases",
    "list_presets",
    "page_users",
    "rename_base",
    "settle_run",
    "users_after",
]
