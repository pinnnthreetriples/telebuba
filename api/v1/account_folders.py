"""Account folders — thin routes over ``services.account_folders``.

A refused name answers 409 with ``folder_name_taken`` as the envelope ``message``; an
unknown folder answers 404. Membership writes are idempotent and report only what
they changed, so the SPA's undo reverts exactly that.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path
from fastapi import status as http_status

from api.errors import error_responses
from schemas.account_folders import (
    AccountFolder,
    AccountFolderAccounts,
    AccountFolderChange,
    AccountFolders,
    AccountFolderWrite,
)
from services import account_folders as folders

router = APIRouter(prefix="/account-folders", tags=["account-folders"])

FolderIdPath = Annotated[str, Path(min_length=1, max_length=64)]

_NOT_FOUND = "folder not found"


def _not_found() -> HTTPException:
    return HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)


def _name_taken(exc: folders.AccountFolderNameTakenError) -> HTTPException:
    return HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=exc.code)


@router.get("", response_model=AccountFolders, operation_id="listAccountFolders")
async def list_account_folders() -> AccountFolders:
    """Folders in creation order with their account counts, plus the views' counts."""
    return await folders.list_folders()


@router.post(
    "",
    response_model=AccountFolder,
    operation_id="createAccountFolder",
    responses=error_responses(409),
)
async def create_account_folder(body: AccountFolderWrite) -> AccountFolder:
    try:
        return await folders.create_folder(body.name)
    except folders.AccountFolderNameTakenError as exc:
        raise _name_taken(exc) from exc


@router.patch(
    "/{folder_id}",
    response_model=AccountFolder,
    operation_id="renameAccountFolder",
    responses=error_responses(404, 409),
)
async def rename_account_folder(folder_id: FolderIdPath, body: AccountFolderWrite) -> AccountFolder:
    try:
        renamed = await folders.rename_folder(folder_id, body.name)
    except folders.AccountFolderNameTakenError as exc:
        raise _name_taken(exc) from exc
    if renamed is None:
        raise _not_found()
    return renamed


@router.delete(
    "/{folder_id}",
    status_code=http_status.HTTP_204_NO_CONTENT,
    operation_id="deleteAccountFolder",
    responses=error_responses(404),
)
async def delete_account_folder(folder_id: FolderIdPath) -> None:
    """Delete a folder; its accounts stay in the list and in their other folders."""
    if not await folders.delete_folder(folder_id):
        raise _not_found()


@router.post(
    "/{folder_id}/accounts",
    response_model=AccountFolderChange,
    operation_id="addAccountsToFolder",
    responses=error_responses(404),
)
async def add_accounts_to_folder(
    folder_id: FolderIdPath, body: AccountFolderAccounts
) -> AccountFolderChange:
    """Add accounts; ``account_ids`` in the answer are only the newly added ones."""
    change = await folders.add_accounts(folder_id, body.account_ids)
    if change is None:
        raise _not_found()
    return change


@router.post(
    "/{folder_id}/accounts/remove",
    response_model=AccountFolderChange,
    operation_id="removeAccountsFromFolder",
    responses=error_responses(404),
)
async def remove_accounts_from_folder(
    folder_id: FolderIdPath, body: AccountFolderAccounts
) -> AccountFolderChange:
    """Take accounts out; ``account_ids`` in the answer are only those that were in it."""
    change = await folders.remove_accounts(folder_id, body.account_ids)
    if change is None:
        raise _not_found()
    return change
