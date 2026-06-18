"""NiceGUI Logs page.

UI-thin per non-negotiable #1: the handler validates input, calls
``services.logs.load_logs_page``, and renders. Refreshes every 3 seconds via
``ui.timer`` per ``.mex/context/logging.md``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from nicegui import context, ui

from schemas.logs import LogEntry, LogFilter
from services.logs import load_logs_page

if TYPE_CHECKING:
    from nicegui.elements.label import Label


_STATUS_OPTIONS = {
    "all": "Все",
    "success": "Успех",
    "warning": "Предупреждение",
    "error": "Ошибка",
}
_STATUS_LABEL_RU = {
    "success": "Успех",
    "warning": "Предупреждение",
    "error": "Ошибка",
}
_LEVEL_LABEL_RU = {
    "INFO": "Инфо",
    "WARNING": "Предупреждение",
    "ERROR": "Ошибка",
}
_POLL_INTERVAL_SECONDS = 3.0
_TABLE_PAGE_SIZE = 25
_TABLE_COLUMNS = [
    {"name": "created_at", "label": "Время", "field": "created_at", "sortable": True},
    {"name": "level", "label": "Уровень", "field": "level", "sortable": True},
    {"name": "status", "label": "Статус", "field": "status", "sortable": True},
    {"name": "account_id", "label": "Аккаунт", "field": "account_id", "sortable": True},
    {"name": "event", "label": "Событие", "field": "event", "sortable": True},
    {"name": "extra", "label": "Данные", "field": "extra"},
]


def register_logs_page() -> None:  # pragma: no cover
    @ui.page("/logs", title="Telebuba — Логи")
    async def logs_page() -> None:
        await _render_logs_page()


async def _render_logs_page() -> None:  # pragma: no cover
    ui.query("body").classes("bg-slate-50 text-slate-950")

    with (
        ui.row().classes(
            "w-full items-center justify-between px-4 py-2 bg-white "
            "text-slate-950 border-b border-slate-200",
        ),
        ui.row().classes("items-center gap-4"),
    ):
        ui.label("Telebuba").classes("text-lg font-semibold")
        ui.link("Аккаунты", "/").classes(
            "text-sm text-slate-600 hover:text-slate-900 no-underline",
        )
        ui.link("Прогрев", "/warming").classes(
            "text-sm text-slate-600 hover:text-slate-900 no-underline",
        )
        ui.link("Логи", "/logs").classes(
            "text-sm font-medium text-slate-900 no-underline",
        )

    with ui.column().classes("w-full max-w-[1400px] mx-auto p-4 gap-3"):
        ui.label("Логи").classes("text-lg font-semibold")

        with ui.row().classes("w-full items-center gap-3"):
            total_label = _metric_label("Всего", "0")
            success_label = _metric_label("Успех", "0")
            warning_label = _metric_label("Предупреждения", "0")
            error_label = _metric_label("Ошибки", "0")

        with ui.row().classes("w-full items-center gap-2"):
            account_input = ui.input(placeholder="ID аккаунта").props("dense outlined clearable")
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
        _set_metric(total_label, "Всего", state.summary.total)
        _set_metric(success_label, "Успех", state.summary.success)
        _set_metric(warning_label, "Предупреждения", state.summary.warning)
        _set_metric(error_label, "Ошибки", state.summary.error)

    async def refresh_from_event(_event: object = None) -> None:
        await refresh()

    account_input.on("update:model-value", refresh_from_event)
    status_select.on("update:model-value", refresh_from_event)

    await refresh()
    # See features/warming/__init__.py for why the lambda wrapper is necessary.
    poll_timer = ui.timer(_POLL_INTERVAL_SECONDS, refresh)
    context.client.on_disconnect(lambda: poll_timer.cancel())  # noqa: PLW0108


def _to_row_dict(entry: LogEntry) -> dict[str, object]:  # pragma: no cover
    return {
        "id": entry.id,
        "created_at": entry.created_at,
        "level": _LEVEL_LABEL_RU.get(entry.level, entry.level),
        "status": _STATUS_LABEL_RU.get(entry.status, entry.status),
        "account_id": entry.account_id or "—",
        "event": entry.event.replace("_", " "),
        "extra": json.dumps(entry.extra, default=str, ensure_ascii=False, sort_keys=True),
    }


def _metric_label(label: str, value: str) -> Label:  # pragma: no cover
    return ui.label(f"{label}: {value}").classes(
        "px-3 py-2 bg-white border border-slate-200 rounded text-sm",
    )


def _set_metric(element: Label, label: str, value: int) -> None:  # pragma: no cover
    element.set_text(f"{label}: {value}")
