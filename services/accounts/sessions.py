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
from services.accounts._import_rollback import discard_imported_session
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
        #
        # Two blockers, two messages, because the remedies are different and the
        # single message named the wrong one. A rollback that removed the row but
        # could not unlink the file (``file_kept``) left NO account in the UI, and
        # the operator was still told to "delete the account first" — pointing at
        # something that does not exist, with no way forward.
        if await fetch_account(session_name) is not None:
            msg = (
                f"An account with session {session_name!r} already exists. "
                "Delete it first if you want to replace the credentials."
            )
            raise SessionAlreadyExistsError(msg)
        if session_path.exists():
            msg = (
                f"A session file named {session_path.name!r} is already on disk with no "
                "account using it. Remove that file from the sessions directory to import."
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
    """Roll back an import whose ``add_account`` refused, row and file together.

    ``add_account`` can refuse either side of its commit. Before it: the
    ``session_name`` may be taken by a DIFFERENT ``account_id`` whose own file is
    gone, so neither check above sees the conflict. After it: the row is already
    committed and the fingerprint / readback / ``log_event`` steps that follow can
    still fail. Removing only the file on that second path would delete a live
    account's sole credential, so ``discard_imported_session`` removes both — see
    that module for the ordering and why a row present here is ours to delete.

    Either way the file must not simply be left: the ``session_path.exists()``
    check above turns a retryable failure into a permanent one, with no account
    row for the operator to delete from the UI.
    """
    result = await discard_imported_session(session_name, path)
    if result.outcome != "clean":
        # ``error_type`` is the class of the failure THIS event is named after — the
        # rollback's own — matching ``tdata_rollback_unlink_failed``. The residual
        # rides ``reason``, which the SPA renders beside it
        # (``shared/lib/log/eventReason.ts``), so the operator reads "session file
        # left on disk with no account · PermissionError" and knows what to clear. It
        # replaced a ``kept`` key the UI showed nowhere, next to a ``cause_type`` it
        # showed nowhere either.
        await log_event(
            "ERROR",
            "account_session_import_rollback_failed",
            extra={
                "session_name": session_name,
                "reason": result.outcome,
                "error_type": result.error_type,
            },
        )
        return
    # Same key, same rule: the class of the failure THIS event is named after. Here
    # that is the IMPORT's cause — the rollback itself succeeded, so it has no error
    # of its own to report. Putting ``result.outcome`` here instead would render a raw
    # ``"clean"`` in the operator's reason column, which is why it must stay pinned.
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
