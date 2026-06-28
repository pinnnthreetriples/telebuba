"""Accounts page composition root.

Registers the NiceGUI ``/`` route and wires the header / table / controller
together. Everything here is rendering glue (``pragma: no cover``); the
business logic it calls lives in ``services.accounts`` and is unit-tested.
"""

from __future__ import annotations

from nicegui import ui

from features.accounts._controller import _AccountsController
from features.accounts._header import _build_header
from features.accounts._proxy_pool import _build_proxy_pool
from features.accounts._styles import ACCOUNTS_CSS
from features.accounts._table_section import _build_table_section
from features.shared import page_shell

# Register the accounts-only CSS once for every client (same shared-CSS pattern
# as ``features/warming`` and ``features/neurocomment``).
ui.add_css(ACCOUNTS_CSS, shared=True)


def register_accounts_page() -> None:  # pragma: no cover
    @ui.page("/", title="Telebuba")
    async def accounts_page() -> None:
        await _render_accounts_page()


async def _render_accounts_page() -> None:  # pragma: no cover
    selected_ids: set[str] = set()
    with page_shell("/"):
        # Order per spec §C.1: proxy-pool card → page header → stats → table.
        _build_proxy_pool()
        header = _build_header()
        section = _build_table_section(selected_ids)
    ctrl = _AccountsController(header, section, selected_ids)

    header.refresh.on("click", ctrl.refresh)
    header.add.on("click", ctrl.open_add)
    header.check_selected.on("click", ctrl.check_selected)
    header.check_all.on("click", ctrl.check_all)
    header.query_input.on("update:model-value", ctrl.refresh)
    header.status_select.on("update:model-value", ctrl.refresh)
    section.table.on("check_one", ctrl.check_one)
    section.table.on("edit_profile", ctrl.open_profile)
    section.table.on("edit_proxy", ctrl.open_proxy)
    section.table.on("delete_account", ctrl.open_delete)

    await ctrl.refresh()
