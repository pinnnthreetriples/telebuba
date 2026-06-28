"""Page-header row for the accounts page (design-spec §C.1.2).

Renders the ``<h1>`` "Аккаунты" on the left and the right-aligned action
cluster — an expandable search box and the four toolbar actions (refresh /
add / check-selected / check-all) — restyled as ``tb-*`` buttons. The old
global top bar moved into ``features/shared/page_shell``; this row only owns
the page-local actions. Returns the widget handles so
:mod:`features.accounts._page` can wire the click events (unchanged).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from nicegui import ui

if TYPE_CHECKING:
    from nicegui.elements.button import Button
    from nicegui.elements.input import Input
    from nicegui.elements.select import Select

# Status-filter options (existing functionality kept; not in the design's
# header but real behaviour we must not drop). RU labels match the table's.
_STATUS_OPTIONS = {
    "all": "Все статусы",
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
class _HeaderWidgets:  # pragma: no cover
    """Page-header widget handles wired by :mod:`features.accounts._page`."""

    refresh: Button
    add: Button
    check_selected: Button
    check_all: Button
    query_input: Input
    status_select: Select


def _build_header() -> _HeaderWidgets:  # pragma: no cover
    with ui.row().classes("w-full items-center justify-between").style("margin-bottom:2px"):
        ui.html('<h1 class="tb-h1">Аккаунты</h1>')
        with ui.row().classes("items-center").style("gap:8px"):
            status_select = (
                ui.select(_STATUS_OPTIONS, value="all")
                .props("dense borderless options-dense")
                .classes("tb-input")
                .style("width:170px;padding:2px 12px")
            )
            query_input = (
                ui.input(placeholder="Поиск по номеру…")
                .props("dense borderless clearable debounce=300")
                .classes("tb-input tb-acc-search")
                .style("width:210px;padding:7px 12px")
            )
            check_selected = ui.button("Проверить выбранные").classes("tb-btn tb-btn-white")
            check_selected.props("flat no-caps text-color=dark")
            check_all = ui.button("Проверить все").classes("tb-btn tb-btn-white")
            check_all.props("flat no-caps text-color=dark")
            refresh = (
                ui.button(icon="refresh")
                .classes("tb-icon-btn")
                .props(
                    "flat round text-color=grey-7",
                )
            )
            refresh.tooltip("Обновить")
            add = ui.button("Аккаунт", icon="add").classes("tb-btn tb-btn-primary")
            add.props("flat no-caps text-color=white")
    return _HeaderWidgets(
        refresh=refresh,
        add=add,
        check_selected=check_selected,
        check_all=check_all,
        query_input=query_input,
        status_select=status_select,
    )
