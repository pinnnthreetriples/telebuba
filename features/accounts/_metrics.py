"""Metric tile rendering for the accounts page header strip."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import ui

if TYPE_CHECKING:
    from nicegui.elements.label import Label

    from features.accounts._table_section import _TableSection
    from schemas.accounts import AccountSummary


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
