"""Session-file and tdata-archive import flows for the accounts domain.

``convert_tdata_zip`` and ``check_telegram_session`` are imported here at module
scope (rather than from a deeper helper) so tests can monkeypatch them at
``services.accounts.sessions.<name>`` — the public functions in this module
resolve those names from their module globals at call time.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import TYPE_CHECKING

from core.config import settings
from core.db import fetch_account, update_account_from_session_check
from core.logging import log_event
from core.session_json import SessionJsonError, fingerprint_from_meta, parse_seller_json
from core.tdata_import import convert_tdata_zip
from core.telegram_client import check_telegram_session
from schemas.accounts import (
    AccountCheckRequest,
    AccountCreate,
    AccountRead,
    AccountSessionFileImport,
    AccountSessionImportResult,
)
from schemas.tdata import TdataConvertRequest, TdataImportResult
from schemas.telegram_session import TelegramSessionCheckRequest
from services.accounts._import_locks import import_lock
from services.accounts._import_rollback import discard_imported_session
from services.accounts._result import AccountActionError
from services.accounts._tdata import (
    SessionAlreadyExistsError,
)
from services.accounts._tdata import (
    import_account_tdata as _tdata_import,
)
from services.accounts._uploads import _session_filename, _write_session_file
from services.accounts.devices import assert_device_configured
from services.accounts.lifecycle import add_account
from services.accounts.twofa import record_account_twofa
from services.inbox_runtime import start_account_inbox, stop_account_inbox
from services.lifecycle_journal import record_session_death

if TYPE_CHECKING:
    from schemas.session_json import SellerSessionMeta

__all__ = [
    "SessionAlreadyExistsError",
    "check_account_session",
    "import_account_session",
    "import_account_tdata",
    "parse_session_json",
]

_NON_DIGITS = re.compile(r"\D")


def parse_session_json(content: bytes) -> SellerSessionMeta:
    """The seller JSON's fields; an unreadable file is the refusal ``session_json_invalid``."""
    try:
        return parse_seller_json(content)
    except SessionJsonError as exc:
        raise AccountActionError(str(exc)) from None


def _assert_json_phone_matches(session_name: str, meta: SellerSessionMeta | None) -> None:
    """The JSON must describe THIS session: its phone is the file stem's digits."""
    if meta is None or meta.phone is None:
        return
    if _NON_DIGITS.sub("", session_name) != meta.phone:
        code = "session_json_phone_mismatch"
        raise AccountActionError(code)


async def _record_imported_twofa(account_id: str, password: str) -> bool:
    """Best-effort: verify the JSON's password with Telegram, then store it.

    ``False`` for every way it did not end up stored — wrong password, no 2FA on the
    account, an unreachable Telegram, a failed write. The import has already
    succeeded and an unverified password is never kept; the operator can retry from
    the account's 2FA card. Only the outcome is logged, never the password.
    """
    try:
        return await record_account_twofa(account_id, password)
    except Exception as exc:  # noqa: BLE001 - the import already succeeded
        await log_event(
            "WARNING",
            "account_twofa_record_skipped",
            account_id=account_id,
            extra={"reason": exc.code if isinstance(exc, AccountActionError) else "failed"},
        )
        return False


async def import_account_session(data: AccountSessionFileImport) -> AccountSessionImportResult:
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
    meta = data.meta
    _assert_json_phone_matches(session_name, meta)
    # An Android seller device makes the session a mobile one, whatever the wizard said.
    session_device = "mobile" if meta is not None and meta.is_android else data.session_device
    assert_device_configured(session_device)
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
            registered_at=data.registered_at,
            session_device=session_device,
            phone=meta.phone if meta is not None else None,
            device=fingerprint_from_meta(session_name, meta) if meta is not None else None,
        )
        await asyncio.to_thread(_write_session_file, session_path, data.content)
        try:
            account = await add_account(create)
        except Exception as exc:
            await _discard_orphaned_session(session_path, session_name, exc)
            raise
    recorded = (
        await _record_imported_twofa(account.account_id, meta.password)
        if meta is not None and meta.password
        else None
    )
    return AccountSessionImportResult(**account.model_dump(), twofa_recorded=recorded)


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
    saved = await update_account_from_session_check(result)
    # Journal the death once (``record_session_death`` knows when it is news).
    await record_session_death(account, result.status)
    if result.status in {"alive", "frozen"}:
        await start_account_inbox(saved.account_id)
    elif result.status in {"unauthorized", "session_error", "account_error"}:
        await stop_account_inbox(saved.account_id)
    return saved


async def import_account_tdata(data: TdataConvertRequest) -> TdataImportResult:
    """Atomic ``.session`` import from a tdata.zip — see :mod:`._tdata`."""
    accounts = await _tdata_import(
        data,
        convert=convert_tdata_zip,
        add_account=add_account,
        check_account_session=check_account_session,
    )
    return TdataImportResult(accounts=accounts)
