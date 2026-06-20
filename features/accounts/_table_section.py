"""Metric strip + filter controls + accounts table for the page.

Pure rendering: builds the widgets and returns them in a :class:`_TableSection`
bundle so :class:`features.accounts._controller._AccountsController` can read
and refresh them. The table's column templates and per-row helpers live in
:mod:`._table`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from nicegui import ui

from features.accounts._metrics import _metric_label
from features.accounts._table import (
    _ACTIONS_TEMPLATE,
    _DEVICE_TEMPLATE,
    _PROXY_TEMPLATE,
    _STATUS_BADGE_TEMPLATE,
    _TABLE_COLUMNS,
    _TELEGRAM_TEMPLATE,
    _remember_selection,
)

if TYPE_CHECKING:
    from nicegui.elements.input import Input
    from nicegui.elements.label import Label
    from nicegui.elements.select import Select
    from nicegui.elements.table import Table

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

        table = (
            ui.table(
                columns=_TABLE_COLUMNS,
                rows=[],
                row_key="account_id",
                selection="multiple",
                pagination=_TABLE_PAGE_SIZE,
                on_select=lambda event: _remember_selection(event.selection, selected_ids),
            )
            .props("dense flat")
            .classes("w-full")
        )
        table.add_slot("body-cell-status", _STATUS_BADGE_TEMPLATE)
        table.add_slot("body-cell-telegram", _TELEGRAM_TEMPLATE)
        table.add_slot("body-cell-device", _DEVICE_TEMPLATE)
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
