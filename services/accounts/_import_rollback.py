"""Undo a half-finished session import so the DB row and the ``.session`` never disagree.

Shared by both importers (``sessions.import_account_session`` and
``_tdata._rollback_tdata_import``) because both had the same hole, and a rollback
that removes only one of the pair is worse than no rollback at all.

Why the row has to go with the file: ``add_account`` is not atomic.
``core.repositories.accounts._create_account`` COMMITS its row, and three more
fallible awaits follow it — ``get_or_create_device_fingerprint``,
``list_accounts``, ``log_event``. Every one of those is a SQLite operation on a
single-file datastore whose routine failures are "database is locked", "disk I/O
error" and "database or disk is full", so a post-commit raise is ordinary, not
exotic. Unlinking the file on that path would leave a LIVE account whose
``.session`` — normally the only copy of its Telegram auth key — is gone, and a
retry that the surviving row then refuses. Removing both restores the state that
existed before the attempt, which is the only state a retry can proceed from.

Order is deliberate, and deliberately NOT the order ``lifecycle.remove_account``
uses. That one unlinks before deleting the row, so a Windows ``PermissionError``
aborts it *before* the row goes and it never leaves an orphan file for a deleted
account — correct there, where removing the row is the goal. Here the goal is to
get back to "neither exists", so each step is ordered by which state its failure
leaves behind:

* row first — if the delete fails we stop and KEEP the file, so the surviving row
  still owns a working credential;
* file second — if the unlink fails, the file is an orphan that blocks a retry,
  but nothing has been destroyed.

Neither branch can destroy a working account. The reverse order has a branch that
can, which is the bug this module exists to close.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Literal

from core.db import delete_account, fetch_account
from core.telegram_client import removing_client

if TYPE_CHECKING:
    from pathlib import Path

# What survived the rollback. ``clean`` is the only outcome a retry can use.
RollbackOutcome = Literal["clean", "row_kept", "file_kept"]

__all__ = ["RollbackOutcome", "discard_imported_session"]


async def discard_imported_session(account_id: str, session_path: Path) -> RollbackOutcome:
    """Remove ``account_id``'s row and ``session_path``, reporting what survived.

    The row is deleted only when one is actually there. Both callers hold that
    account's import lock across their own "does this already exist?" pre-check
    and this rollback, and both refuse the import outright when the pre-check
    finds a row — so a row present *here* is one the same import created.

    The unlink itself verifies nothing about the file's identity: it removes
    whatever now sits at ``session_path``. It does not need to. The path is
    ``session_dir/<session_name>.session`` and the row being deleted alongside it
    is the account of that same name, so the pair leaves together whatever raced
    into that path — and migration #7's unique index already forbids a second
    account from owning that session name.

    Runs under ``removing_client`` for the reason account removal does: without
    the tombstone a concurrent borrower (post listener, warming loop, channel
    discovery) can reach ``get_client`` between these awaits and re-open — or with
    Telethon's ``SQLiteSession``, re-create — the very file being removed.
    """
    async with removing_client(account_id):
        try:
            if await fetch_account(account_id) is not None:
                await delete_account(account_id)
        except Exception:  # noqa: BLE001 — leaving the file beats orphaning a live row.
            return "row_kept"
        try:
            await asyncio.to_thread(session_path.unlink, missing_ok=True)
        except OSError:
            return "file_kept"
    return "clean"
