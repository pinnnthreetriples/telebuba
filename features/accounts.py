from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from nicegui import ui

from core.config import settings
from core.db import create_account, list_accounts, update_account_from_session_check
from core.device_fingerprint import get_or_create_device_fingerprint
from core.tdata_import import convert_tdata_zip
from core.telegram_client import check_telegram_session
from schemas.accounts import (
    AccountCheckRequest,
    AccountCreate,
    AccountFilter,
    AccountRead,
    AccountSessionFileImport,
    AccountsTableState,
    AccountStatus,
    AccountSummary,
    AccountTableRow,
)
from schemas.tdata import TdataConvertRequest
from schemas.telegram_session import TelegramSessionCheckRequest

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from nicegui.elements.button import Button
    from nicegui.elements.label import Label
    from nicegui.events import UploadEventArguments


_PERMANENT_ISSUES = {"unauthorized", "session_error", "account_error"}
_TEMPORARY_ISSUES = {"flood_wait", "network_error", "proxy_error", "unknown_error"}
_TABLE_PAGE_SIZE = 15
_STATUS_OPTIONS = [
    "all",
    "new",
    "alive",
    "unauthorized",
    "session_error",
    "account_error",
    "flood_wait",
    "network_error",
    "proxy_error",
    "unknown_error",
]
_TABLE_COLUMNS = [
    {"name": "label", "label": "Account", "field": "label", "sortable": True},
    {"name": "status", "label": "Status", "field": "status", "sortable": True},
    {"name": "telegram", "label": "Telegram", "field": "telegram", "sortable": True},
    {"name": "session", "label": "Session", "field": "session", "sortable": True},
    {"name": "device", "label": "Device", "field": "device", "sortable": True},
    {"name": "last_checked", "label": "Checked", "field": "last_checked", "sortable": True},
]


async def add_account(data: AccountCreate) -> AccountRead:
    account = await create_account(data)
    await get_or_create_device_fingerprint(account.account_id)
    saved = await list_accounts()
    for item in saved.accounts:
        if item.account_id == account.account_id:
            return item
    return account


async def import_account_session(data: AccountSessionFileImport) -> AccountRead:
    filename = _session_filename(data.filename)
    session_name = Path(filename).stem
    session_path = settings.session_dir / filename
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
    result = await convert_tdata_zip(data, settings.session_dir)
    if result.status != "ok":
        msg = f"tdata import failed: {result.status}"
        if result.error:
            msg = f"{msg} — {result.error}"
        raise ValueError(msg)
    if not result.accounts:
        msg = "tdata contained no accounts"
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
    filtered = [_account for _account in accounts.accounts if _matches_filter(_account, data)]
    return AccountsTableState(
        rows=[_to_table_row(account) for account in filtered],
        summary=_summarize(accounts.accounts),
    )


def register_accounts_page() -> None:  # pragma: no cover
    @ui.page("/", title="Telebuba")
    async def accounts_page() -> None:
        await _render_accounts_page()


async def _render_accounts_page() -> None:  # pragma: no cover
    selected_ids: set[str] = set()

    ui.query("body").classes("bg-slate-50 text-slate-950")

    buttons = _build_header()
    with ui.column().classes("w-full max-w-[1400px] mx-auto p-4 gap-3"):
        with ui.row().classes("w-full items-center gap-3"):
            total_label = _metric_label("Total", "0")
            alive_label = _metric_label("Alive", "0")
            issue_label = _metric_label("Needs attention", "0")
            temp_label = _metric_label("Temporary", "0")
            new_label = _metric_label("New", "0")

        with ui.row().classes("w-full items-center gap-2"):
            query_input = ui.input(placeholder="Search").props("dense outlined clearable")
            query_input.classes("w-80 max-w-full")
            status_select = ui.select(_STATUS_OPTIONS, value="all").props("dense outlined")
            status_select.classes("w-48")

        table = ui.table(
            columns=_TABLE_COLUMNS,
            rows=[],
            row_key="account_id",
            selection="multiple",
            pagination=_TABLE_PAGE_SIZE,
            on_select=lambda event: _remember_selection(event.selection, selected_ids),
        ).classes("w-full")

    async def refresh() -> None:
        state = await load_accounts_table(
            AccountFilter(query=query_input.value or "", status=status_select.value),
        )
        table.rows = [row.model_dump() for row in state.rows]
        table.update()
        _set_metric(total_label, "Total", state.summary.total)
        _set_metric(alive_label, "Alive", state.summary.alive)
        _set_metric(issue_label, "Needs attention", state.summary.permanent_issue)
        _set_metric(temp_label, "Temporary", state.summary.temporary_issue)
        _set_metric(new_label, "New", state.summary.never_checked)

    async def check_selected() -> None:
        await _check_accounts(selected_ids)
        await refresh()

    async def check_all() -> None:
        await _check_accounts({str(row["account_id"]) for row in table.rows})
        await refresh()

    async def open_add_dialog() -> None:
        await _open_add_dialog(refresh)

    async def refresh_from_event(_event: object = None) -> None:
        await refresh()

    async def check_selected_from_event(_event: object = None) -> None:
        await check_selected()

    async def check_all_from_event(_event: object = None) -> None:
        await check_all()

    async def open_add_dialog_from_event(_event: object = None) -> None:
        await open_add_dialog()

    buttons.refresh.on("click", refresh_from_event)
    buttons.add.on("click", open_add_dialog_from_event)
    buttons.check_selected.on("click", check_selected_from_event)
    buttons.check_all.on("click", check_all_from_event)
    query_input.on("update:model-value", refresh_from_event)
    status_select.on("update:model-value", refresh_from_event)

    await refresh()


class _ToolbarButtons:  # pragma: no cover
    def __init__(
        self,
        refresh: Button,
        add: Button,
        check_selected: Button,
        check_all: Button,
    ) -> None:
        self.refresh = refresh
        self.add = add
        self.check_selected = check_selected
        self.check_all = check_all


def _build_header() -> _ToolbarButtons:  # pragma: no cover
    with ui.row().classes(
        "w-full items-center justify-between px-4 py-2 bg-white "
        "text-slate-950 border-b border-slate-200",
    ):
        ui.label("Accounts").classes("text-lg font-semibold")
        with ui.row().classes("items-center gap-2"):
            refresh_button = ui.button(icon="refresh", color="grey-8")
            refresh_button.tooltip("Refresh")
            add_button = ui.button(icon="add", color="primary")
            add_button.tooltip("Add account")
            check_selected_button = ui.button(icon="fact_check", color="primary")
            check_selected_button.tooltip("Check selected")
            check_all_button = ui.button(icon="playlist_add_check", color="primary")
            check_all_button.tooltip("Check all")
    return _ToolbarButtons(refresh_button, add_button, check_selected_button, check_all_button)


async def _open_add_dialog(refresh: Callable[[], Awaitable[None]]) -> None:  # pragma: no cover
    with ui.dialog() as dialog, ui.column().classes("bg-white p-4 gap-3 w-96 max-w-full"):
        ui.label("Add account").classes("text-base font-semibold")
        label = ui.input("Display name").props("dense outlined")

        async def handle_session_upload(event: UploadEventArguments) -> None:
            try:
                await import_account_session(
                    AccountSessionFileImport(
                        filename=event.file.name,
                        content=await event.file.read(),
                        label=label.value or None,
                    ),
                )
            except ValueError as exc:
                ui.notify(str(exc), type="warning")
                return
            dialog.close()
            await refresh()

        async def handle_tdata_upload(event: UploadEventArguments) -> None:
            try:
                accounts = await import_account_tdata(
                    TdataConvertRequest(
                        filename=event.file.name,
                        content=await event.file.read(),
                        label=label.value or None,
                    ),
                )
            except ValueError as exc:
                ui.notify(str(exc), type="warning")
                return
            dialog.close()
            ui.notify(f"Imported {len(accounts)} account(s) from tdata", type="positive")
            await refresh()

        ui.upload(
            label="Upload .session",
            multiple=False,
            max_file_size=20_000_000,
            auto_upload=True,
            on_upload=handle_session_upload,
            on_rejected=lambda _event: ui.notify("Session file rejected", type="warning"),
        ).props('accept=".session"').classes("w-full")

        ui.label("or").classes("self-center text-xs text-slate-500")

        ui.upload(
            label="Upload tdata.zip",
            multiple=False,
            max_file_size=100_000_000,
            auto_upload=True,
            on_upload=handle_tdata_upload,
            on_rejected=lambda _event: ui.notify("tdata zip rejected", type="warning"),
        ).props('accept=".zip"').classes("w-full")

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button(icon="close", color="grey-7", on_click=dialog.close).tooltip("Cancel")
    dialog.open()


async def _check_accounts(account_ids: set[str]) -> None:  # pragma: no cover
    if not account_ids:
        ui.notify("No accounts selected", type="warning")
        return
    for account_id in sorted(account_ids):
        await check_account_session(AccountCheckRequest(account_id=account_id))
    ui.notify("Session check finished", type="positive")


def _metric_label(label: str, value: str) -> Label:  # pragma: no cover
    return ui.label(f"{label}: {value}").classes(
        "px-3 py-2 bg-white border border-slate-200 rounded text-sm",
    )


def _set_metric(element: Label, label: str, value: int) -> None:  # pragma: no cover
    element.set_text(f"{label}: {value}")


def _remember_selection(selection: list[dict[str, object]], selected_ids: set[str]) -> None:
    selected_ids.clear()
    selected_ids.update(str(row["account_id"]) for row in selection)


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
        telegram=_telegram_label(account),
        session=account.session_name or account.account_id,
        device=_device_label(account),
        last_checked=account.last_checked_at or "never",
    )


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
