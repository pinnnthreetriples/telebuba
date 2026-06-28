"""Stat counters + accounts table (design-spec §C.1.3 / §C.1.4).

Pure rendering: builds the five stat tiles and the accounts table, returning
them in a :class:`_TableSection` bundle so
:class:`features.accounts._controller._AccountsController` can refresh them.
The page header (H1 + search + actions) lives in :mod:`._header`; the
proxy-pool card in :mod:`._proxy_pool`; the table's column templates and
per-row helpers in :mod:`._table`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from nicegui import ui

from features.accounts._metrics import _build_stat
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
    from nicegui.elements.label import Label
    from nicegui.elements.table import Table

_TABLE_PAGE_SIZE = 15


@dataclass
class _TableSection:  # pragma: no cover
    """The five stat tiles and the accounts table built for the page."""

    total_label: Label
    alive_label: Label
    issue_label: Label
    temp_label: Label
    new_label: Label
    table: Table


def _build_table_section(selected_ids: set[str]) -> _TableSection:  # pragma: no cover
    # Stat counters — design-spec §C.1.3 colours. The schema gives five coarse
    # counts (total / alive / permanent / temporary / never-checked); we keep
    # those semantics and dress them in the spec's stat palette.
    with ui.row().classes("w-full").style("gap:10px;flex-wrap:wrap"):
        total_label = _build_stat("Всего", "#0B0B0C")
        alive_label = _build_stat("Живые", "#2E7D55")
        issue_label = _build_stat("Требуют внимания", "#C0473F")
        temp_label = _build_stat("Временные проблемы", "#9A7B22")
        new_label = _build_stat("Новые", "#0066FF")

    with (
        ui.element("div")
        .classes("tb-card tb-acc-table w-full")
        .style(
            "padding:0;overflow:hidden",
        ),
        ui.element("div").classes("tb-scroll w-full").style("overflow-x:auto"),
    ):
        table = (
            ui.table(
                columns=_TABLE_COLUMNS,
                rows=[],
                row_key="account_id",
                selection="multiple",
                pagination=_TABLE_PAGE_SIZE,
                on_select=lambda event: _remember_selection(event.selection, selected_ids),
            )
            .props("flat")
            .classes("w-full")
            .style("min-width:880px")
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
        table=table,
    )
