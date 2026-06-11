"""NiceGUI accounts page.

UI-thin per non-negotiable #1. Each handler is a small pass-through to
``services.accounts``. All orchestration, validation, persistence, and Telegram
interaction live in the service layer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, cast

from nicegui import ui

from schemas.accounts import (
    AccountCheckRequest,
    AccountFilter,
    AccountProfileUpdateRequest,
    AccountSessionFileImport,
    health_for_status,
)
from schemas.proxy import AccountProxyDelete, AccountProxyUpsert
from schemas.tdata import TdataConvertRequest
from services.accounts import (
    check_account_session,
    delete_account_proxy,
    import_account_session,
    import_account_tdata,
    load_accounts_table,
    save_account_proxy,
    update_account_profile,
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


def _col(
    name: str,
    label: str,
    field: str,
    *,
    sortable: bool = True,
    align: str = "left",
) -> dict[str, object]:
    return {"name": name, "label": label, "field": field, "sortable": sortable, "align": align}


_TABLE_COLUMNS = [
    _col("label", "Account", "label"),
    _col("status", "Status", "status"),
    _col("telegram", "Telegram", "telegram"),
    _col("session", "Session", "session"),
    _col("device", "Device", "device"),
    _col("proxy", "Proxy", "proxy"),
    _col("last_checked", "Checked", "last_checked"),
    _col("actions", "", "account_id", sortable=False, align="right"),
]

_STATUS_BADGE_TEMPLATE = """
<q-td :props="props">
  <q-chip
    :color="{ok: 'positive', warn: 'warning', fail: 'negative'}[props.row.health] || 'grey-5'"
    text-color="white"
    dense
    :label="props.row.status"
  />
</q-td>
"""

_ACTIONS_TEMPLATE = """
<q-td :props="props">
  <q-btn
    dense round flat
    icon="refresh"
    color="primary"
    @click="() => $parent.$emit('check_one', props.row.account_id)"
  >
    <q-tooltip>Check this account</q-tooltip>
  </q-btn>
  <q-btn
    dense round flat
    icon="manage_accounts"
    color="primary"
    @click="() => $parent.$emit('edit_profile', props.row)"
  >
    <q-tooltip>Edit profile</q-tooltip>
  </q-btn>
  <q-btn
    dense round flat
    icon="vpn_key"
    color="primary"
    @click="() => $parent.$emit('edit_proxy', props.row)"
  >
    <q-tooltip>Proxy settings</q-tooltip>
  </q-btn>
</q-td>
"""

_NOTIFY_TYPE_BY_HEALTH: dict[str, Literal["positive", "warning", "negative"]] = {
    "ok": "positive",
    "warn": "warning",
    "fail": "negative",
}


def register_accounts_page() -> None:  # pragma: no cover
    @ui.page("/", title="Telebuba")
    async def accounts_page() -> None:
        await _render_accounts_page()


# NiceGUI page glue: wiring is naturally long, logic stays in services/.
async def _render_accounts_page() -> None:  # pragma: no cover  # noqa: C901, PLR0915
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
        table.add_slot("body-cell-status", _STATUS_BADGE_TEMPLATE)
        table.add_slot("body-cell-actions", _ACTIONS_TEMPLATE)

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

    async def check_one(event: object) -> None:
        account_id = _account_id_from_event(event)
        if not account_id:
            ui.notify("Could not resolve account id", type="negative")
            return
        spinner = ui.notification(
            f"Checking {account_id}…",
            spinner=True,
            timeout=None,
            close_button=False,
        )
        try:
            account = await check_account_session(
                AccountCheckRequest(account_id=account_id),
            )
            await refresh()
        finally:
            spinner.dismiss()
        ui.notify(
            f"{account_id}: {account.status}",
            type=_NOTIFY_TYPE_BY_HEALTH[health_for_status(account.status)],
        )

    async def open_add_dialog() -> None:
        await _open_add_dialog(refresh)

    async def open_profile_dialog(event: object) -> None:
        row = _row_from_event(event)
        if not row:
            ui.notify("Could not resolve account", type="negative")
            return
        await _open_profile_dialog(row, refresh)

    async def open_proxy_dialog(event: object) -> None:
        row = _row_from_event(event)
        if not row:
            ui.notify("Could not resolve account", type="negative")
            return
        await _open_proxy_dialog(row, refresh)

    async def refresh_from_event(_event: object = None) -> None:
        await refresh()

    async def check_selected_from_event(_event: object = None) -> None:
        await check_selected()

    async def check_all_from_event(_event: object = None) -> None:
        await check_all()

    async def open_add_dialog_from_event(_event: object = None) -> None:
        await open_add_dialog()

    async def open_profile_dialog_from_event(event: object) -> None:
        await open_profile_dialog(event)

    async def open_proxy_dialog_from_event(event: object) -> None:
        await open_proxy_dialog(event)

    buttons.refresh.on("click", refresh_from_event)
    buttons.add.on("click", open_add_dialog_from_event)
    buttons.check_selected.on("click", check_selected_from_event)
    buttons.check_all.on("click", check_all_from_event)
    query_input.on("update:model-value", refresh_from_event)
    status_select.on("update:model-value", refresh_from_event)
    table.on("check_one", check_one)
    table.on("edit_profile", open_profile_dialog_from_event)
    table.on("edit_proxy", open_proxy_dialog_from_event)

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
        with ui.row().classes("items-center gap-4"):
            ui.label("Telebuba").classes("text-lg font-semibold")
            ui.link("Accounts", "/").classes(
                "text-sm font-medium text-slate-900 no-underline",
            )
            ui.link("Logs", "/logs").classes(
                "text-sm text-slate-600 hover:text-slate-900 no-underline",
            )
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
            # Telegram Desktop tdata can be hundreds of MB with cached emoji
            # and user_data — generous cap to fit a real archive.
            max_file_size=1_000_000_000,
            auto_upload=True,
            on_upload=handle_tdata_upload,
            on_rejected=lambda _event: ui.notify(
                "tdata zip rejected (must be a .zip, max 1 GB)",
                type="warning",
            ),
        ).props('accept=".zip"').classes("w-full")

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button(icon="close", color="grey-7", on_click=dialog.close).tooltip("Cancel")
    dialog.open()


async def _open_profile_dialog(
    row: dict[str, object],
    refresh: Callable[[], Awaitable[None]],
) -> None:  # pragma: no cover
    account_id = str(row["account_id"])
    with ui.dialog() as dialog, ui.column().classes("bg-white p-4 gap-3 w-96 max-w-full"):
        ui.label("Edit profile").classes("text-base font-semibold")
        first_name = ui.input("First name", value=str(row.get("first_name") or "")).props(
            "dense outlined",
        )
        last_name = ui.input("Last name", value=str(row.get("last_name") or "")).props(
            "dense outlined clearable",
        )
        username = ui.input("Username", value=str(row.get("username") or "")).props(
            "dense outlined clearable prefix=@",
        )
        bio = ui.textarea("Bio", value=str(row.get("bio") or "")).props("dense outlined")

        async def save() -> None:
            name = (first_name.value or "").strip()
            if not name:
                ui.notify("First name is required", type="warning")
                return
            try:
                await update_account_profile(
                    AccountProfileUpdateRequest(
                        account_id=account_id,
                        first_name=name,
                        last_name=(last_name.value or "").strip(),
                        username=(username.value or "").strip().removeprefix("@"),
                        bio=(bio.value or "").strip(),
                    ),
                )
            except ValueError as exc:
                ui.notify(str(exc), type="negative")
                return
            dialog.close()
            ui.notify("Profile updated", type="positive")
            await refresh()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button(icon="close", color="grey-7", on_click=dialog.close).tooltip("Cancel")
            ui.button(icon="save", color="primary", on_click=save).tooltip("Save profile")
    dialog.open()


async def _open_proxy_dialog(
    row: dict[str, object],
    refresh: Callable[[], Awaitable[None]],
) -> None:  # pragma: no cover
    account_id = str(row["account_id"])
    with ui.dialog() as dialog, ui.column().classes("bg-white p-4 gap-3 w-96 max-w-full"):
        ui.label("Proxy settings").classes("text-base font-semibold")
        proxy_type = ui.select(
            ["socks5", "http"],
            value=str(row.get("proxy_type") or "socks5"),
            label="Type",
        ).props("dense outlined")
        host = ui.input("Host", value=str(row.get("proxy_host") or "")).props("dense outlined")
        port = ui.number("Port", value=_proxy_port_value(row), min=1, max=65_535).props(
            "dense outlined",
        )
        username = ui.input("Username").props("dense outlined clearable")
        password = ui.input("Password").props("dense outlined clearable type=password")

        async def save() -> None:
            host_value = (host.value or "").strip()
            if not host_value:
                ui.notify("Proxy host is required", type="warning")
                return
            try:
                await save_account_proxy(
                    AccountProxyUpsert(
                        account_id=account_id,
                        proxy_type=proxy_type.value,
                        host=host_value,
                        port=int(port.value or 0),
                        username=(username.value or "").strip() or None,
                        password=(password.value or "").strip() or None,
                    ),
                )
            except ValueError as exc:
                ui.notify(str(exc), type="negative")
                return
            dialog.close()
            ui.notify("Proxy saved", type="positive")
            await refresh()

        async def remove() -> None:
            await delete_account_proxy(AccountProxyDelete(account_id=account_id))
            dialog.close()
            ui.notify("Proxy removed", type="positive")
            await refresh()

        with ui.row().classes("w-full justify-between gap-2"):
            ui.button(icon="delete", color="negative", on_click=remove).tooltip("Remove proxy")
            with ui.row().classes("gap-2"):
                ui.button(icon="close", color="grey-7", on_click=dialog.close).tooltip("Cancel")
                ui.button(icon="save", color="primary", on_click=save).tooltip("Save proxy")
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


def _account_id_from_event(event: object) -> str:
    """Extract the account_id payload from a Quasar custom event arg.

    NiceGUI surfaces the Vue ``$emit`` payload as ``event.args`` (str when only
    one arg was emitted; list when multiple). Be tolerant of both shapes.
    """
    args = getattr(event, "args", event)
    if isinstance(args, list) and args:
        args = args[0]
    return str(args) if args is not None else ""


def _row_from_event(event: object) -> dict[str, object]:
    args = getattr(event, "args", event)
    if isinstance(args, list) and args:
        args = args[0]
    return cast("dict[str, object]", args) if isinstance(args, dict) else {}


def _proxy_port_value(row: dict[str, object]) -> int:
    value = row.get("proxy_port")
    return value if isinstance(value, int) else 1080
