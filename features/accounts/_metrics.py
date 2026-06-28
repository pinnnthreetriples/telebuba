"""Stat-tile rendering for the accounts page (design-spec §C.1.3).

Each tile is a ``.tb-stat`` card: a big colour-coded number over a muted
label. ``_build_stat`` returns the number ``Label`` so the controller can
update it via ``_refresh_metrics`` without re-rendering the card.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import ui

if TYPE_CHECKING:
    from nicegui.elements.label import Label

    from features.accounts._table_section import _TableSection
    from schemas.accounts import AccountSummary


def _build_stat(label: str, color: str) -> Label:  # pragma: no cover
    """Render one stat tile, returning its (colour-coded) value label.

    ``tabular-nums`` keeps the digit column from shifting as counts update.
    """
    with ui.element("div").classes("tb-stat").style("flex:1 1 0"):
        value = (
            ui.label("0")
            .classes("tb-stat-num")
            .style(
                f"color:{color};font-variant-numeric:tabular-nums",
            )
        )
        ui.label(label).classes("tb-stat-label")
    return value


def _set_metric(element: Label, value: int) -> None:  # pragma: no cover
    element.set_text(str(value))


def _refresh_metrics(section: _TableSection, summary: AccountSummary) -> None:  # pragma: no cover
    _set_metric(section.total_label, summary.total)
    _set_metric(section.alive_label, summary.alive)
    _set_metric(section.issue_label, summary.permanent_issue)
    _set_metric(section.temp_label, summary.temporary_issue)
    _set_metric(section.new_label, summary.never_checked)
