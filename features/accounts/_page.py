"""Accounts page composition root.

Registers the NiceGUI ``/`` route and wires the header / table / controller
together. Everything here is rendering glue (``pragma: no cover``); the
business logic it calls lives in ``services.accounts`` and is unit-tested.
"""

from __future__ import annotations

from nicegui import ui

from features.accounts._controller import _AccountsController
from features.accounts._header import _build_header
from features.accounts._table_section import _build_table_section


def register_accounts_page() -> None:  # pragma: no cover
    @ui.page("/", title="Telebuba")
    async def accounts_page() -> None:
        await _render_accounts_page()


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
    section.table.on("delete_account", ctrl.open_delete)

    await ctrl.refresh()
