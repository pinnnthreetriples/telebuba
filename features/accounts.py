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
from schemas.profile_media import (
    AccountProfileMusicUpload,
    AccountProfilePhotoUpload,
    AccountStoryUpload,
)
from schemas.proxy import AccountProxyCheckRequest, AccountProxyDelete, AccountProxyUpsert
from schemas.tdata import TdataConvertRequest
from services.accounts import (
    add_account_profile_music,
    check_account_proxy,
    check_account_session,
    delete_account_proxy,
    import_account_session,
    import_account_tdata,
    load_accounts_table,
    post_account_story,
    save_account_proxy,
    set_account_profile_photo,
    update_account_profile,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

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

_PROXY_TEMPLATE = """
<q-td :props="props">
  <div v-if="props.row.proxy_host" class="column q-gutter-xs">
    <div class="row items-center no-wrap q-gutter-xs">
      <q-chip
        v-if="props.row.proxy_status === 'tcp_working'"
        dense
        square
        color="positive"
        text-color="white"
        label="Working"
      />
      <q-chip
        v-else-if="props.row.proxy_status === 'failed'"
        dense
        square
        color="negative"
        text-color="white"
        label="Failed"
      />
      <q-chip
        v-else
        dense
        square
        color="grey-6"
        text-color="white"
        label="Unknown"
      />
      <span class="text-weight-medium">{{ props.row.proxy }}</span>
    </div>
    <div
      v-if="props.row.proxy_country_name || props.row.proxy_country_code || props.row.proxy_exit_ip"
      class="text-caption text-grey-7"
    >
      {{ props.row.proxy_country_name || props.row.proxy_country_code || '' }}
      <span v-if="props.row.proxy_exit_ip"> · {{ props.row.proxy_exit_ip }}</span>
    </div>
  </div>
  <q-chip v-else dense outline square color="grey-6" label="No proxy" />
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
        table.add_slot("body-cell-proxy", _PROXY_TEMPLATE)
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
            ui.link("Warming", "/warming").classes(
                "text-sm text-slate-600 hover:text-slate-900 no-underline",
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


async def _open_profile_dialog(  # noqa: PLR0915
    row: dict[str, object],
    refresh: Callable[[], Awaitable[None]],
) -> None:  # pragma: no cover
    account_id = str(row["account_id"])
    with ui.dialog() as dialog, ui.column().classes("bg-white p-4 gap-3 w-[560px] max-w-full"):
        ui.label("Edit profile").classes("text-base font-semibold")
        with ui.tabs().classes("w-full") as tabs:
            text_tab = ui.tab("Text")
            photo_tab = ui.tab("Photo")
            story_tab = ui.tab("Story")
            music_tab = ui.tab("Music")
        with ui.tab_panels(tabs, value=text_tab).classes("w-full"):
            with ui.tab_panel(text_tab).classes("gap-3"):
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
                    ui.notify("Profile updated", type="positive")
                    await refresh()

                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button(icon="close", color="grey-7", on_click=dialog.close).tooltip(
                        "Cancel",
                    )
                    ui.button(icon="save", color="primary", on_click=save).tooltip("Save profile")

            with ui.tab_panel(photo_tab).classes("gap-3"):

                async def handle_photo_upload(event: UploadEventArguments) -> None:
                    try:
                        await set_account_profile_photo(
                            AccountProfilePhotoUpload(
                                account_id=account_id,
                                filename=event.file.name,
                                content=await event.file.read(),
                            ),
                        )
                    except ValueError as exc:
                        ui.notify(str(exc), type="negative")
                        return
                    ui.notify("Profile photo updated", type="positive")

                ui.upload(
                    label="Upload profile photo",
                    multiple=False,
                    max_file_size=10_000_000,
                    auto_upload=True,
                    on_upload=handle_photo_upload,
                    on_rejected=lambda _event: ui.notify("Profile photo rejected", type="warning"),
                ).props('accept=".jpg,.jpeg,.png,.webp"').classes("w-full")

            with ui.tab_panel(story_tab).classes("gap-3"):
                story_kind = ui.select(
                    {"image": "Image", "video": "Video"},
                    value="image",
                    label="Media",
                ).props("dense outlined")
                story_privacy = ui.select(
                    {"contacts": "Contacts", "close_friends": "Close friends", "public": "Public"},
                    value="contacts",
                    label="Privacy",
                ).props("dense outlined")
                story_caption = ui.textarea("Caption").props("dense outlined")
                protect_story = ui.checkbox("Protect content", value=False)

                async def handle_story_upload(event: UploadEventArguments) -> None:
                    try:
                        await post_account_story(
                            AccountStoryUpload(
                                account_id=account_id,
                                filename=event.file.name,
                                content=await event.file.read(),
                                media_kind=story_kind.value,
                                caption=(story_caption.value or "").strip() or None,
                                privacy_preset=story_privacy.value,
                                protect_content=bool(protect_story.value),
                            ),
                        )
                    except ValueError as exc:
                        ui.notify(str(exc), type="negative")
                        return
                    ui.notify("Story posted", type="positive")

                ui.upload(
                    label="Upload story media",
                    multiple=False,
                    max_file_size=100_000_000,
                    auto_upload=True,
                    on_upload=handle_story_upload,
                    on_rejected=lambda _event: ui.notify("Story media rejected", type="warning"),
                ).props('accept=".jpg,.jpeg,.png,.webp,.mp4,.mov"').classes("w-full")

            with ui.tab_panel(music_tab).classes("gap-3"):
                music_title = ui.input("Title").props("dense outlined clearable")
                music_performer = ui.input("Performer").props("dense outlined clearable")

                async def handle_music_upload(event: UploadEventArguments) -> None:
                    try:
                        await add_account_profile_music(
                            AccountProfileMusicUpload(
                                account_id=account_id,
                                filename=event.file.name,
                                content=await event.file.read(),
                                title=(music_title.value or "").strip() or None,
                                performer=(music_performer.value or "").strip() or None,
                            ),
                        )
                    except ValueError as exc:
                        ui.notify(str(exc), type="negative")
                        return
                    ui.notify("Profile music added", type="positive")

                ui.upload(
                    label="Upload music",
                    multiple=False,
                    max_file_size=30_000_000,
                    auto_upload=True,
                    on_upload=handle_music_upload,
                    on_rejected=lambda _event: ui.notify("Music rejected", type="warning"),
                ).props('accept=".mp3,.m4a"').classes("w-full")
    dialog.open()


async def _open_proxy_dialog(
    row: dict[str, object],
    refresh: Callable[[], Awaitable[None]],
) -> None:  # pragma: no cover
    account_id = str(row["account_id"])
    with ui.dialog() as dialog, ui.column().classes("bg-white p-4 gap-3 w-[460px] max-w-full"):
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
        with ui.column().classes(
            "w-full gap-1 rounded border border-slate-200 bg-slate-50 px-3 py-2",
        ):
            status_label = ui.label(_proxy_dialog_status(row)).classes("text-sm font-medium")
            geo_label = ui.label(_proxy_dialog_geo(row)).classes("text-xs text-slate-600")
            error_label = ui.label(_proxy_dialog_error(row)).classes("text-xs text-red-600")

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

        async def check_proxy() -> None:
            spinner = ui.notification(
                "Checking proxy route...",
                spinner=True,
                timeout=None,
                close_button=False,
            )
            try:
                proxy = await check_account_proxy(AccountProxyCheckRequest(account_id=account_id))
            except ValueError as exc:
                ui.notify(str(exc), type="warning")
                return
            finally:
                spinner.dismiss()
            checked_row = {
                "proxy_status": proxy.status,
                "proxy_last_checked_at": proxy.last_checked_at,
                "proxy_last_error": proxy.last_error,
                "proxy_exit_ip": proxy.exit_ip,
                "proxy_country_code": proxy.country_code,
                "proxy_country_name": proxy.country_name,
            }
            status_label.set_text(_proxy_dialog_status(checked_row))
            geo_label.set_text(_proxy_dialog_geo(checked_row))
            error_label.set_text(_proxy_dialog_error(checked_row))
            ui.notify(
                "Proxy works" if proxy.status == "tcp_working" else "Proxy failed",
                type="positive" if proxy.status == "tcp_working" else "negative",
            )
            await refresh()

        async def remove() -> None:
            await delete_account_proxy(AccountProxyDelete(account_id=account_id))
            dialog.close()
            ui.notify("Proxy removed", type="positive")
            await refresh()

        with ui.row().classes("w-full justify-between gap-2"):
            ui.button(icon="delete", color="negative", on_click=remove).tooltip("Remove proxy")
            with ui.row().classes("gap-2"):
                ui.button(icon="travel_explore", color="primary", on_click=check_proxy).tooltip(
                    "Check proxy",
                )
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


def _proxy_dialog_status(row: Mapping[str, object]) -> str:
    status = str(row.get("proxy_status") or "unknown")
    labels = {
        "tcp_working": "Status: working",
        "failed": "Status: failed",
        "unknown": "Status: not checked",
    }
    checked_at = str(row.get("proxy_last_checked_at") or "").strip()
    suffix = f" | checked {checked_at}" if checked_at else ""
    return f"{labels.get(status, 'Status: not checked')}{suffix}"


def _proxy_dialog_geo(row: Mapping[str, object]) -> str:
    parts = [
        str(row.get("proxy_country_name") or "").strip(),
        str(row.get("proxy_country_code") or "").strip(),
        str(row.get("proxy_exit_ip") or "").strip(),
    ]
    value = " | ".join(part for part in parts if part)
    return f"Route: {value}" if value else "Route: no country/IP yet"


def _proxy_dialog_error(row: Mapping[str, object]) -> str:
    error = str(row.get("proxy_last_error") or "").strip()
    return f"Error: {error}" if error else ""
