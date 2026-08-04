"""Session-file and tdata-archive import flows for the accounts domain.

``convert_tdata_zip`` and ``check_telegram_session`` are imported here at module
scope (rather than from a deeper helper) so tests can monkeypatch them at
``services.accounts.sessions.<name>`` — the public functions in this module
resolve those names from their module globals at call time.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from core.config import settings
from core.db import fetch_account, update_account_from_session_check
from core.logging import log_event
from core.tdata_import import convert_tdata_zip
from core.telegram_client import check_telegram_session
from schemas.accounts import (
    AccountCheckRequest,
    AccountCreate,
    AccountRead,
    AccountSessionFileImport,
)
from schemas.tdata import TdataConvertRequest, TdataImportResult
from schemas.telegram_session import TelegramSessionCheckRequest
from services.accounts._import_locks import import_lock
from services.accounts._tdata import (
    SessionAlreadyExistsError,
)
from services.accounts._tdata import (
    import_account_tdata as _tdata_import,
)
from services.accounts._uploads import _session_filename, _write_session_file
from services.accounts.lifecycle import add_account

__all__ = [
    "SessionAlreadyExistsError",
    "check_account_session",
    "import_account_session",
    "import_account_tdata",
]


async def import_account_session(data: AccountSessionFileImport) -> AccountRead:
    # Service-layer guardrail: ``.session`` files are effectively credentials.
    # The UI may attempt to validate size first, but a CLI / scheduler caller
    # can bypass that — re-check here.
    max_bytes = settings.profile_media.session_max_bytes
    if not data.content:
        msg = "Session file is empty"
        raise ValueError(msg)
    if len(data.content) > max_bytes:
        msg = f"Session file is too large (>{max_bytes} bytes)"
        raise ValueError(msg)
    filename = _session_filename(data.filename)
    session_name = Path(filename).stem
    session_path = settings.telegram.session_dir / filename
    # Serialize same-name imports across the check→write→add sequence so two
    # concurrent uploads of the same session can't both pass the existence check
    # and have the second overwrite the first's credential.
    async with import_lock(session_name):
        # Refuse to overwrite credentials. Check by account_id (DB) AND by file
        # presence on disk — either being present means there is already an
        # account whose session we would clobber.
        if await fetch_account(session_name) is not None or session_path.exists():
            msg = (
                f"An account with session {session_name!r} already exists. "
                "Delete it first if you want to replace the credentials."
            )
            raise SessionAlreadyExistsError(msg)
        # Validate the id BEFORE the credential lands on disk. ``_session_filename``
        # only enforces the suffix and a non-empty stem, so ``..session`` (stem
        # ``.``) passed it and was written, then rejected by ``AccountCreate``'s
        # charset pattern — leaving orphaned bytes in ``session_dir`` with no row to
        # delete them. Building the model first turns that into a pure refusal.
        create = AccountCreate(
            account_id=session_name,
            label=data.label,
            session_name=session_name,
        )
        await asyncio.to_thread(_write_session_file, session_path, data.content)
        try:
            return await add_account(create)
        except Exception as exc:
            await _discard_orphaned_session(session_path, session_name, exc)
            raise


async def _discard_orphaned_session(path: Path, session_name: str, cause: Exception) -> None:
    """Remove a ``.session`` written for an import that then failed to add its row.

    ``add_account`` can still refuse after the write — the ``session_name`` may be
    taken by a DIFFERENT ``account_id`` whose own file is gone, so neither check
    above sees it — and the bytes then have no row to own them. Left behind, the
    ``session_path.exists()`` check turns that retryable failure into a permanent
    one: every retry is refused and there is no account for the operator to delete.

    Only ever removes a file THIS call created, while still holding that session's
    import lock, so a working account's credential can never be the target. Same
    reasoning as ``_tdata._rollback_tdata_import``.
    """
    try:
        await asyncio.to_thread(path.unlink)
    except OSError as exc:
        await log_event(
            "ERROR",
            "account_session_import_rollback_failed",
            extra={"session_name": session_name, "error_type": type(exc).__name__},
        )
        return
    await log_event(
        "WARNING",
        "account_session_import_rolled_back",
        extra={"session_name": session_name, "error_type": type(cause).__name__},
    )


async def check_account_session(data: AccountCheckRequest) -> AccountRead:
    account = await fetch_account(data.account_id)
    if account is None:
        msg = f"Unknown account: {data.account_id}"
        raise ValueError(msg)
    result = await check_telegram_session(
        TelegramSessionCheckRequest(
            account_id=account.account_id,
            session_name=account.session_name,
        ),
    )
    return await update_account_from_session_check(result)


async def import_account_tdata(data: TdataConvertRequest) -> TdataImportResult:
    """Atomic ``.session`` import from a tdata.zip — see :mod:`._tdata`."""
    accounts = await _tdata_import(
        data,
        convert=convert_tdata_zip,
        add_account=add_account,
        check_account_session=check_account_session,
    )
    return TdataImportResult(accounts=accounts)
