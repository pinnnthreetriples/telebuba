"""Business logic for the accounts domain.

Pure async functions: validate input, talk to ``core/*`` adapters, return
Pydantic models. No NiceGUI, no SQLAlchemy, no Telethon — those live in
``core/*``. UI handlers in ``features/accounts.py`` are thin pass-throughs.

Public API:

- :func:`add_account` — create the account row and provision its immutable
  device fingerprint.
- :func:`import_account_session` — accept a ``.session`` upload, persist it,
  then call ``add_account``.
- :func:`import_account_tdata` — convert a ``tdata.zip`` upload via
  ``core/tdata_import``, register each contained account, run a session check.
- :func:`check_account_session` — re-verify one account against Telegram.
- :func:`load_accounts_table` — render the accounts table state (filter +
  metric tiles) for the UI.

Per non-negotiable #11, callers in ``features/`` and in any future scheduler
take this module's public functions directly.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from core.config import settings
from core.db import create_account, list_accounts, update_account_from_session_check
from core.device_fingerprint import get_or_create_device_fingerprint
from core.logging import log_event
from core.tdata_import import convert_tdata_zip
from core.telegram_client import check_telegram_session
from schemas.accounts import (
    AccountCheckRequest,
    AccountCreate,
    AccountFilter,
    AccountHealth,
    AccountRead,
    AccountSessionFileImport,
    AccountsTableState,
    AccountStatus,
    AccountSummary,
    AccountTableRow,
)
from schemas.telegram_session import TelegramSessionCheckRequest

if TYPE_CHECKING:
    from schemas.tdata import TdataConvertRequest


_PERMANENT_ISSUES = {"unauthorized", "session_error", "account_error"}
_TEMPORARY_ISSUES = {"flood_wait", "network_error", "proxy_error", "unknown_error"}


async def add_account(data: AccountCreate) -> AccountRead:
    account = await create_account(data)
    await get_or_create_device_fingerprint(account.account_id)
    saved = await list_accounts()
    persisted = next(
        (item for item in saved.accounts if item.account_id == account.account_id),
        account,
    )
    await log_event(
        "INFO",
        "account_added",
        account_id=persisted.account_id,
        extra={"session_name": persisted.session_name},
    )
    return persisted


async def import_account_session(data: AccountSessionFileImport) -> AccountRead:
    filename = _session_filename(data.filename)
    session_name = Path(filename).stem
    session_path = settings.telegram.session_dir / filename
    await asyncio.to_thread(_write_session_file, session_path, data.content)
    return await add_account(
        AccountCreate(account_id=session_name, label=data.label, session_name=session_name),
    )


async def import_account_tdata(data: TdataConvertRequest) -> list[AccountRead]:
    """Convert a tdata.zip payload to one or more .session files and register each account.

    Every successfully written session is added to the DB and immediately session-checked.
    Returns the post-check ``AccountRead`` rows. Raises ``ValueError`` with a human-readable
    message when the conversion itself failed.
    """
    result = await convert_tdata_zip(data, settings.telegram.session_dir)
    if result.status != "ok":
        msg = f"tdata import failed: {result.status}"
        if result.error:
            msg = f"{msg} — {result.error}"
        await log_event(
            "ERROR",
            "tdata_import_failed",
            extra={"status": result.status, "error": result.error},
        )
        raise ValueError(msg)
    if not result.accounts:
        msg = "tdata contained no accounts"
        await log_event("WARNING", "tdata_no_accounts", extra={"filename": data.filename})
        raise ValueError(msg)

    checked: list[AccountRead] = []
    for summary in result.accounts:
        session_name = Path(summary.session_path).stem
        account_id = str(summary.user_id) if summary.user_id is not None else session_name
        await add_account(
            AccountCreate(
                account_id=account_id,
                label=data.label or account_id,
                session_name=session_name,
            ),
        )
        checked.append(
            await check_account_session(AccountCheckRequest(account_id=account_id)),
        )
    await log_event(
        "INFO",
        "tdata_import_completed",
        extra={"imported": len(checked)},
    )
    return checked


async def check_account_session(data: AccountCheckRequest) -> AccountRead:
    accounts = await list_accounts()
    account = next(item for item in accounts.accounts if item.account_id == data.account_id)
    result = await check_telegram_session(
        TelegramSessionCheckRequest(
            account_id=account.account_id,
            session_name=account.session_name,
        ),
    )
    return await update_account_from_session_check(result)


async def load_accounts_table(data: AccountFilter) -> AccountsTableState:
    accounts = await list_accounts()
    filtered = [account for account in accounts.accounts if _matches_filter(account, data)]
    return AccountsTableState(
        rows=[_to_table_row(account) for account in filtered],
        summary=_summarize(accounts.accounts),
    )


def _session_filename(filename: str) -> str:
    name = Path(filename).name
    if Path(name).suffix.lower() != ".session":
        msg = "Upload a .session file"
        raise ValueError(msg)
    if not Path(name).stem:
        msg = "Session file name is empty"
        raise ValueError(msg)
    return name


def _write_session_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _matches_filter(account: AccountRead, data: AccountFilter) -> bool:
    if data.status not in ("all", account.status):
        return False
    if not data.query:
        return True
    haystack = " ".join(
        value or ""
        for value in (
            account.account_id,
            account.label,
            account.phone,
            account.username,
            account.first_name,
            account.last_name,
            account.session_name,
        )
    ).lower()
    return data.query.lower() in haystack


def _summarize(accounts: list[AccountRead]) -> AccountSummary:
    return AccountSummary(
        total=len(accounts),
        alive=sum(account.status == "alive" for account in accounts),
        permanent_issue=sum(account.status in _PERMANENT_ISSUES for account in accounts),
        temporary_issue=sum(account.status in _TEMPORARY_ISSUES for account in accounts),
        never_checked=sum(account.status == "new" for account in accounts),
    )


def _to_table_row(account: AccountRead) -> AccountTableRow:
    return AccountTableRow(
        account_id=account.account_id,
        label=account.label or account.account_id,
        status=_status_label(account.status),
        health=_health_for(account.status),
        telegram=_telegram_label(account),
        session=account.session_name or account.account_id,
        device=_device_label(account),
        last_checked=account.last_checked_at or "never",
    )


def _health_for(status: AccountStatus) -> AccountHealth:
    """Map an ``AccountStatus`` to a coarse traffic-light health value.

    Used by the UI for the colored status badge: green for working accounts,
    amber for retry-soon situations, red for permanent failures.
    """
    if status == "alive":
        return "ok"
    if status in _PERMANENT_ISSUES:
        return "fail"
    return "warn"


def _status_label(status: AccountStatus) -> str:
    labels = {
        "new": "New",
        "alive": "Alive",
        "unauthorized": "Unauthorized",
        "session_error": "Session error",
        "account_error": "Account error",
        "flood_wait": "Flood wait",
        "network_error": "Network",
        "proxy_error": "Proxy",
        "unknown_error": "Unknown",
    }
    return labels[status]


def _telegram_label(account: AccountRead) -> str:
    name = " ".join(part for part in (account.first_name, account.last_name) if part)
    username = f"@{account.username}" if account.username else ""
    phone = account.phone or ""
    return " | ".join(part for part in (name, username, phone) if part) or "-"


def _device_label(account: AccountRead) -> str:
    return (
        " | ".join(
            part
            for part in (
                account.device_model,
                account.device_system_version,
                account.device_app_version,
            )
            if part
        )
        or "-"
    )
