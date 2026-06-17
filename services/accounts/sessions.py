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
    # Refuse to overwrite credentials. Check by account_id (DB) AND by file
    # presence on disk — either being present means there is already an
    # account whose session we would clobber.
    if await fetch_account(session_name) is not None or session_path.exists():
        msg = (
            f"An account with session {session_name!r} already exists. "
            "Delete it first if you want to replace the credentials."
        )
        raise SessionAlreadyExistsError(msg)
    await asyncio.to_thread(_write_session_file, session_path, data.content)
    return await add_account(
        AccountCreate(account_id=session_name, label=data.label, session_name=session_name),
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
