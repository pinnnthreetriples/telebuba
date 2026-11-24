"""Put the newest vault copy back as an account's own ``.session`` file.

Only over a file that is missing or holds no key, never over a keyed one. The caller (the
service) holds the account's auth lock and the pool tombstone ``removing_client``; this module
does the file work: a keyless leftover goes to the quarantine first, then the newest version
is decrypted into place.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path as PlainPath
from typing import Literal

from anyio import to_thread

from core.session_file import inspect_session_file
from core.session_vault import (
    SessionVaultError,
    list_versions,
    restore_session,
    session_copies,
    vault_enabled,
)
from core.telegram_client._auth import _login_request, _remove_session_file
from core.telegram_client._client import _session_path

__all__ = ["SessionRestoreRefusedError", "restore_newest_session_copy", "session_restore_state"]

RestoreCode = Literal[
    "session_restore_file_present",
    "session_vault_off",
    "session_restore_no_copy",
    "session_restore_copy_unreadable",
]


class SessionRestoreRefusedError(Exception):
    """A restore was refused; ``code`` is the stable contract."""

    def __init__(self, code: RestoreCode) -> None:
        super().__init__(code)
        self.code: RestoreCode = code


async def _file(account_id: str, session_name: str | None) -> PlainPath:
    return PlainPath(f"{await _session_path(_login_request(account_id, session_name))}.session")


async def session_restore_state(
    account_id: str, session_name: str | None
) -> tuple[int, datetime | None, bool]:
    """``(copies, newest copy's time, restorable)``: restorable = vault on, a copy, no key now."""
    file = await _file(account_id, session_name)
    count, newest = await to_thread.run_sync(session_copies, file.stem)
    state = await to_thread.run_sync(inspect_session_file, file)
    return count, newest, vault_enabled() and count > 0 and state in {"missing", "no_key"}


async def restore_newest_session_copy(account_id: str, session_name: str | None) -> datetime:
    """Restore the newest copy; when it was made. Raises :class:`SessionRestoreRefusedError`."""
    file = await _file(account_id, session_name)
    if await to_thread.run_sync(inspect_session_file, file) not in {"missing", "no_key"}:
        code = "session_restore_file_present"
        raise SessionRestoreRefusedError(code)
    if not vault_enabled():
        code = "session_vault_off"
        raise SessionRestoreRefusedError(code)
    versions = await to_thread.run_sync(list_versions, file.stem)
    if not versions:
        code = "session_restore_no_copy"
        raise SessionRestoreRefusedError(code)
    # A keyless leftover is set aside (quarantine), not overwritten: the restore needs a free name.
    await _remove_session_file(str(file.with_suffix("")))
    try:
        await to_thread.run_sync(restore_session, versions[-1], file)
    except SessionVaultError:
        # Written with another vault key, or damaged: nothing was put in place.
        code = "session_restore_copy_unreadable"
        raise SessionRestoreRefusedError(code) from None
    _count, at = await to_thread.run_sync(session_copies, file.stem)
    return at or datetime.now(UTC)
