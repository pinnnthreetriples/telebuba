"""Account folders: operator-named lists of accounts on the Accounts page.

Many-to-many: an account may sit in several folders. Deleting a folder never deletes an
account; deleting an account drops its memberships (``core.repositories._accounts_delete``).
Names are unique ignoring case, which the repository enforces on a casefolded key.
"""

from __future__ import annotations

from core.logging import log_event
from core.repositories import account_folders as repository
from schemas.account_folders import AccountFolder, AccountFolderChange, AccountFolders

FOLDER_NAME_TAKEN = "folder_name_taken"


class AccountFolderNameTakenError(Exception):
    """Another folder already has this name (case-insensitively)."""

    code = FOLDER_NAME_TAKEN


async def list_folders() -> AccountFolders:
    return await repository.list_folders()


async def create_folder(name: str) -> AccountFolder:
    saved = await repository.create_folder(name)
    return await _landed(saved, "account_folder_created")


async def rename_folder(folder_id: str, name: str) -> AccountFolder | None:
    """Rename a folder; ``None`` when it does not exist."""
    saved = await repository.rename_folder(folder_id, name)
    if saved == "not_found":
        return None
    return await _landed(saved, "account_folder_renamed")


async def delete_folder(folder_id: str) -> bool:
    deleted = await repository.delete_folder(folder_id)
    if deleted:
        await log_event("INFO", "account_folder_deleted", extra={"folder_id": folder_id})
    return deleted


async def add_accounts(folder_id: str, account_ids: list[str]) -> AccountFolderChange | None:
    """Add accounts (idempotent); the change names only the newly added ones."""
    added = await repository.add_members(folder_id, account_ids)
    if added is None:
        return None
    await log_event(
        "INFO", "account_folder_accounts_added", extra={"folder_id": folder_id, "count": len(added)}
    )
    return AccountFolderChange(folder_id=folder_id, account_ids=added)


async def remove_accounts(folder_id: str, account_ids: list[str]) -> AccountFolderChange | None:
    """Take accounts out of a folder; the change names only those that were in it."""
    removed = await repository.remove_members(folder_id, account_ids)
    if removed is None:
        return None
    await log_event(
        "INFO",
        "account_folder_accounts_removed",
        extra={"folder_id": folder_id, "count": len(removed)},
    )
    return AccountFolderChange(folder_id=folder_id, account_ids=removed)


async def _landed(saved: AccountFolder | repository.FolderMiss, event: str) -> AccountFolder:
    if not isinstance(saved, AccountFolder):
        raise AccountFolderNameTakenError(FOLDER_NAME_TAKEN)
    await log_event("INFO", event, extra={"folder_id": saved.folder_id})
    return saved
