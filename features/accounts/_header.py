"""Top toolbar for the accounts page.

Pure UI scaffolding — builds the title bar with the cross-page nav and the
action buttons (refresh / add / check-selected / check-all) and returns the
button handles so :mod:`features.accounts._page` can wire click events.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import ui

from features.shared import TOP_BAR_CLASSES, render_nav

if TYPE_CHECKING:
    from nicegui.elements.button import Button


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
    with ui.row().classes(TOP_BAR_CLASSES):
        render_nav("/")
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
