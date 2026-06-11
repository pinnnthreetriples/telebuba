"""NiceGUI Logs page.

UI-thin per non-negotiable #1: the handler validates input, calls
``services.logs.load_logs_page``, and renders. Refreshes every 3 seconds via
``ui.timer`` per ``.mex/context/logging.md``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from nicegui import ui

from schemas.logs import LogEntry, LogFilter
from services.logs import load_logs_page

if TYPE_CHECKING:
    from nicegui.elements.label import Label


_STATUS_OPTIONS = ["all", "success", "warning", "error"]
_POLL_INTERVAL_SECONDS = 3.0
_TABLE_PAGE_SIZE = 25
_TABLE_COLUMNS = [
    {"name": "created_at", "label": "Time", "field": "created_at", "sortable": True},
    {"name": "level", "label": "Level", "field": "level", "sortable": True},
    {"name": "status", "label": "Status", "field": "status", "sortable": True},
    {"name": "account_id", "label": "Account", "field": "account_id", "sortable": True},
    {"name": "event", "label": "Event", "field": "event", "sortable": True},
    {"name": "extra", "label": "Extra", "field": "extra"},
]


def register_logs_page() -> None:  # pragma: no cover
    @ui.page("/logs", title="Telebuba — Logs")
    async def logs_page() -> None:
        await _render_logs_page()


async def _render_logs_page() -> None:  # pragma: no cover
    ui.query("body").classes("bg-slate-50 text-slate-950")

    with ui.column().classes("w-full max-w-[1400px] mx-auto p-4 gap-3"):
        ui.label("Logs").classes("text-lg font-semibold")

        with ui.row().classes("w-full items-center gap-3"):
            total_label = _metric_label("Total", "0")
            success_label = _metric_label("Success", "0")
            warning_label = _metric_label("Warning", "0")
            error_label = _metric_label("Error", "0")

        with ui.row().classes("w-full items-center gap-2"):
            account_input = ui.input(placeholder="account_id").props("dense outlined clearable")
            account_input.classes("w-64")
            status_select = ui.select(_STATUS_OPTIONS, value="all").props("dense outlined")
            status_select.classes("w-40")

        table = ui.table(
            columns=_TABLE_COLUMNS,
            rows=[],
            row_key="id",
            pagination=_TABLE_PAGE_SIZE,
        ).classes("w-full")

    async def refresh() -> None:
        state = await load_logs_page(
            LogFilter(
                status=status_select.value,
                account_id=account_input.value or "",
            ),
        )
        table.rows = [_to_row_dict(entry) for entry in state.entries]
        table.update()
        _set_metric(total_label, "Total", state.summary.total)
        _set_metric(success_label, "Success", state.summary.success)
        _set_metric(warning_label, "Warning", state.summary.warning)
        _set_metric(error_label, "Error", state.summary.error)

    async def refresh_from_event(_event: object = None) -> None:
        await refresh()

    account_input.on("update:model-value", refresh_from_event)
    status_select.on("update:model-value", refresh_from_event)

    await refresh()
    ui.timer(_POLL_INTERVAL_SECONDS, refresh)


def _to_row_dict(entry: LogEntry) -> dict[str, object]:  # pragma: no cover
    return {
        "id": entry.id,
        "created_at": entry.created_at,
        "level": entry.level,
        "status": entry.status,
        "account_id": entry.account_id or "-",
        "event": entry.event,
        "extra": json.dumps(entry.extra, default=str, sort_keys=True),
    }


def _metric_label(label: str, value: str) -> Label:  # pragma: no cover
    return ui.label(f"{label}: {value}").classes(
        "px-3 py-2 bg-white border border-slate-200 rounded text-sm",
    )


def _set_metric(element: Label, label: str, value: int) -> None:  # pragma: no cover
    element.set_text(f"{label}: {value}")
