"""NiceGUI accounts page.

UI-thin per non-negotiable #1. Each handler is a small pass-through to
``services.accounts``. All orchestration, validation, persistence, and Telegram
interaction live in the service layer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import ui

from schemas.accounts import (
    AccountCheckRequest,
    AccountFilter,
    AccountSessionFileImport,
)
from schemas.tdata import TdataConvertRequest
from services.accounts import (
    check_account_session,
    import_account_session,
    import_account_tdata,
    load_accounts_table,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from nicegui.elements.button import Button
    from nicegui.elements.label import Label
    from nicegui.events import UploadEventArguments


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
