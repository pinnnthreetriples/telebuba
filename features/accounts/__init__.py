"""NiceGUI accounts page.

UI-thin per non-negotiable #1. Each handler is a small pass-through to
``services.accounts``. The page is split into render modules (``_table`` for
column defs / cell templates / row + event helpers, ``_dialogs`` for the
add/profile/proxy dialogs); this module assembles the page and wires events.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from nicegui import ui

from features.accounts._dialogs import (
    _check_accounts,
    _open_add_dialog,
    _open_profile_dialog,
    _open_proxy_dialog,
)
from features.accounts._table import (
    _ACTIONS_TEMPLATE,
    _NOTIFY_TYPE_BY_HEALTH,
    _PROXY_TEMPLATE,
    _STATUS_BADGE_TEMPLATE,
    _TABLE_COLUMNS,
    _account_id_from_event,
    _account_status_label,
    _remember_selection,
    _row_from_event,
    _to_table_row,
)
from schemas.accounts import AccountCheckRequest, AccountFilter, health_for_status
from services.accounts import check_account_session, load_accounts_table

if TYPE_CHECKING:
    from nicegui.elements.button import Button
    from nicegui.elements.input import Input
    from nicegui.elements.label import Label
    from nicegui.elements.select import Select
    from nicegui.elements.table import Table

    from schemas.accounts import AccountSummary

__all__ = ["register_accounts_page"]

_TABLE_PAGE_SIZE = 15
_STATUS_OPTIONS = {
    "all": "Все",
    "new": "Новые",
    "alive": "Живые",
    "unauthorized": "Не авторизованы",
    "session_error": "Ошибка сессии",
    "account_error": "Ошибка аккаунта",
    "flood_wait": "FloodWait",
    "network_error": "Ошибка сети",
    "proxy_error": "Ошибка прокси",
    "unknown_error": "Неизвестная ошибка",
}


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


@dataclass
class _TableSection:  # pragma: no cover
    """The metric tiles, search controls, and table built for the page."""

    total_label: Label
    alive_label: Label
    issue_label: Label
    temp_label: Label
    new_label: Label
    query_input: Input
    status_select: Select
    table: Table


def _metric_label(label: str, value: str) -> Label:  # pragma: no cover
    return ui.label(f"{label}: {value}").classes(
        "px-3 py-2 bg-white border border-slate-200 rounded text-sm",
    )


def _set_metric(element: Label, label: str, value: int) -> None:  # pragma: no cover
    element.set_text(f"{label}: {value}")


def _refresh_metrics(section: _TableSection, summary: AccountSummary) -> None:  # pragma: no cover
    _set_metric(section.total_label, "Всего", summary.total)
    _set_metric(section.alive_label, "Живые", summary.alive)
    _set_metric(section.issue_label, "Требуют внимания", summary.permanent_issue)
    _set_metric(section.temp_label, "Временные проблемы", summary.temporary_issue)
    _set_metric(section.new_label, "Новые", summary.never_checked)


def _build_header() -> _ToolbarButtons:  # pragma: no cover
    with ui.row().classes(
        "w-full items-center justify-between px-4 py-2 bg-white "
        "text-slate-950 border-b border-slate-200",
    ):
        with ui.row().classes("items-center gap-4"):
            ui.label("Telebuba").classes("text-lg font-semibold")
            ui.link("Аккаунты", "/").classes(
                "text-sm font-medium text-slate-900 no-underline",
            )
            ui.link("Прогрев", "/warming").classes(
                "text-sm text-slate-600 hover:text-slate-900 no-underline",
            )
            ui.link("Логи", "/logs").classes(
                "text-sm text-slate-600 hover:text-slate-900 no-underline",
            )
        with ui.row().classes("items-center gap-2"):
            refresh_button = ui.button(icon="refresh", color="grey-8")
            refresh_button.tooltip("Обновить")
            add_button = ui.button(icon="add", color="primary")
            add_button.tooltip("Добавить аккаунт")
            check_selected_button = ui.button(icon="fact_check", color="primary")
            check_selected_button.tooltip("Проверить выбранные")
            check_all_button = ui.button(icon="playlist_add_check", color="primary")
            check_all_button.tooltip("Проверить все")
    return _ToolbarButtons(refresh_button, add_button, check_selected_button, check_all_button)


def _build_table_section(selected_ids: set[str]) -> _TableSection:  # pragma: no cover
    with ui.column().classes("w-full max-w-[1400px] mx-auto p-4 gap-3"):
        with ui.row().classes("w-full items-center gap-3"):
            total_label = _metric_label("Всего", "0")
            alive_label = _metric_label("Живые", "0")
            issue_label = _metric_label("Требуют внимания", "0")
            temp_label = _metric_label("Временные проблемы", "0")
            new_label = _metric_label("Новые", "0")

        with ui.row().classes("w-full items-center gap-2"):
            query_input = ui.input(placeholder="Поиск").props("dense outlined clearable")
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
    return _TableSection(
        total_label=total_label,
        alive_label=alive_label,
        issue_label=issue_label,
        temp_label=temp_label,
        new_label=new_label,
        query_input=query_input,
        status_select=status_select,
        table=table,
    )


def register_accounts_page() -> None:  # pragma: no cover
    @ui.page("/", title="Telebuba")
    async def accounts_page() -> None:
        await _render_accounts_page()


class _AccountsController:  # pragma: no cover
    """Holds page state and event handlers, keeping the page builder flat."""

    def __init__(self, section: _TableSection, selected_ids: set[str]) -> None:
        self._section = section
        self._selected_ids = selected_ids

    async def refresh(self, _event: object = None) -> None:
        section = self._section
        state = await load_accounts_table(
            AccountFilter(
                query=section.query_input.value or "",
                status=section.status_select.value,
            ),
        )
        section.table.rows = [_to_table_row(row.model_dump()) for row in state.rows]
        section.table.update()
        _refresh_metrics(section, state.summary)

    async def check_selected(self, _event: object = None) -> None:
        await _check_accounts(self._selected_ids)
        await self.refresh()

    async def check_all(self, _event: object = None) -> None:
        await _check_accounts({str(row["account_id"]) for row in self._section.table.rows})
        await self.refresh()

    async def check_one(self, event: object) -> None:
        account_id = _account_id_from_event(event)
        if not account_id:
            ui.notify("Не удалось определить ID аккаунта", type="negative")
            return
        spinner = ui.notification(
            f"Проверяем {account_id}…",
            spinner=True,
            timeout=None,
            close_button=False,
        )
        try:
            account = await check_account_session(AccountCheckRequest(account_id=account_id))
            await self.refresh()
        finally:
            spinner.dismiss()
        ui.notify(
            f"{account_id}: {_account_status_label(account.status)}",
            type=_NOTIFY_TYPE_BY_HEALTH[health_for_status(account.status)],
        )

    async def open_add(self, _event: object = None) -> None:
        await _open_add_dialog(self.refresh)

    async def open_profile(self, event: object) -> None:
        row = _row_from_event(event)
        if not row:
            ui.notify("Не удалось определить аккаунт", type="negative")
            return
        await _open_profile_dialog(row, self.refresh)

    async def open_proxy(self, event: object) -> None:
        row = _row_from_event(event)
        if not row:
            ui.notify("Не удалось определить аккаунт", type="negative")
            return
        await _open_proxy_dialog(row, self.refresh)


async def _render_accounts_page() -> None:  # pragma: no cover
    selected_ids: set[str] = set()
    ui.query("body").classes("bg-slate-50 text-slate-950")
    buttons = _build_header()
    section = _build_table_section(selected_ids)
    ctrl = _AccountsController(section, selected_ids)

    buttons.refresh.on("click", ctrl.refresh)
    buttons.add.on("click", ctrl.open_add)
    buttons.check_selected.on("click", ctrl.check_selected)
    buttons.check_all.on("click", ctrl.check_all)
    section.query_input.on("update:model-value", ctrl.refresh)
    section.status_select.on("update:model-value", ctrl.refresh)
    section.table.on("check_one", ctrl.check_one)
    section.table.on("edit_profile", ctrl.open_profile)
    section.table.on("edit_proxy", ctrl.open_proxy)

    await ctrl.refresh()
