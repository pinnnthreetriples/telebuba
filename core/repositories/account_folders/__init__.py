"""Data-access repository for account folders.

The public surface; importing it registers the tables in ``core.db._metadata`` (via
``_tables``). Public functions wrap sync helpers via ``asyncio.to_thread`` and return
Pydantic models — never raw rows.
"""

from __future__ import annotations

from core.repositories.account_folders._filtered import (
    count_filtered_accounts,
    list_filtered_accounts,
)
from core.repositories.account_folders._folders import (
    FolderMiss,
    add_members,
    create_folder,
    delete_folder,
    folder_ids_by_account,
    list_folders,
    remove_members,
    rename_folder,
)

__all__ = [
    "FolderMiss",
    "add_members",
    "count_filtered_accounts",
    "create_folder",
    "delete_folder",
    "folder_ids_by_account",
    "list_filtered_accounts",
    "list_folders",
    "remove_members",
    "rename_folder",
]
