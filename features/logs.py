"""NiceGUI Logs page (redesign — spec C.5).

UI-thin per non-negotiable #1: the handler validates input, calls
``services.logs.load_logs_page``, and renders. Refreshes every 3 seconds via
``ui.timer`` per ``.mex/context/logging.md``.

The visual layer follows ``Telebuba.dc.html`` §C.5: an H1, a segmented status
filter + account pill, and a single ``.tb-card`` four-column table (Время ·
Уровень · Аккаунт · Событие). The table body is one ``ui.html`` rebuilt from a
pure ``_table_body_html`` on each poll; the only branchy logic — the level badge
colour map (``lvlMap``) — lives in the pure ``_level_badge`` helper, unit-tested
in ``tests/features/test_logs_helpers.py``.
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

from nicegui import context, ui

from features.shared import page_shell
from schemas.logs import LogFilter, LogStatusFilter
from services.logs import load_logs_page

if TYPE_CHECKING:
    from schemas.logs import LogEntry

# Status segmented-control: filter value → button label. Honest deviation from
# the mock's "Info/Warn/Error" — the service filters by *status*, not log level,
# so the labels name what the filter actually does.
_STATUS_SEGMENTS: tuple[tuple[LogStatusFilter, str], ...] = (
    ("all", "Все"),
    ("success", "Успех"),
    ("warning", "Предупр."),
    ("error", "Ошибки"),
)
# lvlMap (spec §A / C.5): badge label → (background, text colour). Labels uppercase.
_LVL_MAP: dict[str, tuple[str, str]] = {
    "INFO": ("#E1ECFF", "#0066FF"),
    "WARN": ("#FFF0D2", "#E08700"),
    "ERROR": ("#FDE6E2", "#E5372A"),
    "OK": ("#DDF7E9", "#12A150"),
}
_POLL_INTERVAL_SECONDS = 3.0


def _level_badge(level: str, status: str) -> tuple[str, str, str]:
    """Derive the row's level badge → ``(label, bg, fg)`` from level + status.

    Success rows read OK; otherwise the worst of level/status wins
    (ERROR → WARN → INFO), so a WARNING-level row flagged ``error`` still
    surfaces as ERROR. Pure (no I/O) → unit-tested.
    """
    if status == "success":
        label = "OK"
    elif level == "ERROR" or status == "error":
        label = "ERROR"
    elif level == "WARNING" or status == "warning":
        label = "WARN"
    else:
        label = "INFO"
    bg, fg = _LVL_MAP[label]
    return label, bg, fg


def _row_html(entry: LogEntry) -> str:
    """One ``<tr>`` for the logs table — pure, so it is exercised by the body test."""
    label, bg, fg = _level_badge(entry.level, entry.status)
    badge = (
        f'<span style="display:inline-block;padding:2px 9px;border-radius:9999px;'
        f'font-size:11px;font-weight:600;background:{bg};color:{fg}">{label}</span>'
    )
    time_cell = html.escape(entry.created_at)
    account = html.escape(entry.account_id or "—")
    event = html.escape(entry.event.replace("_", " "))
    return (
        "<tr>"
        f'<td class="tb-mono" style="color:#9A9893;white-space:nowrap">{time_cell}</td>'
        f"<td>{badge}</td>"
        f"<td>{account}</td>"
        f"<td>{event}</td>"
        "</tr>"
    )


def _table_body_html(entries: list[LogEntry]) -> str:
    """The full ``<table>`` markup for the current entries (pure, testable)."""
    head = (
        '<table class="tb-table" style="min-width:760px">'
        "<thead><tr>"
        '<th style="width:120px">Время</th>'
        '<th style="width:110px">Уровень</th>'
        '<th style="width:150px">Аккаунт</th>'
        "<th>Событие</th>"
        "</tr></thead><tbody>"
    )
    if not entries:
        body = (
            '<tr><td colspan="4" style="text-align:center;color:#9A9893;padding:28px 16px">'
            "Записей пока нет</td></tr>"
        )
    else:
        body = "".join(_row_html(entry) for entry in entries)
    return head + body + "</tbody></table>"


def register_logs_page() -> None:  # pragma: no cover
    @ui.page("/logs", title="Telebuba — Логи")
    async def logs_page() -> None:
        await _render_logs_page()


async def _render_logs_page() -> None:  # pragma: no cover
    with page_shell("/logs"):
        ui.html('<h1 class="tb-h1">Логи</h1>')

        status: dict[str, LogStatusFilter] = {"value": "all"}
        seg_buttons: dict[LogStatusFilter, ui.button] = {}

        with ui.row().classes("w-full items-center gap-3"):
            with ui.element("div").classes("tb-seg"):
                for value, label in _STATUS_SEGMENTS:
                    btn = ui.button(label).props("flat no-caps").classes("tb-seg-btn")
                    seg_buttons[value] = btn
            ui.element("div").classes("flex-1")
            account_input = (
                ui.input(placeholder="Поиск по аккаунту…")
                .props("dense borderless clearable")
                .classes("tb-input")
                .style("width:200px")
            )

        with (
            ui.element("div").classes("tb-card").style("padding:0;overflow:hidden"),
            ui.element("div").classes("tb-scroll").style("overflow-x:auto"),
        ):
            table = ui.html(_table_body_html([]))

        def _paint_segments() -> None:
            for value, btn in seg_buttons.items():
                active = value == status["value"]
                btn.classes(add="tb-on" if active else "", remove="" if active else "tb-on")

        async def refresh() -> None:
            state = await load_logs_page(
                LogFilter(
                    status=status["value"],
                    account_id=account_input.value or "",
                ),
            )
            table.set_content(_table_body_html(state.entries))

        async def select_status(value: LogStatusFilter) -> None:
            status["value"] = value
            _paint_segments()
            await refresh()

        for value, btn in seg_buttons.items():
            btn.on("click", lambda _e=None, v=value: select_status(v))
        account_input.on("update:model-value", lambda _e=None: refresh())

        _paint_segments()
        await refresh()
        # See features/warming/__init__.py for why the lambda wrapper is necessary.
        poll_timer = ui.timer(_POLL_INTERVAL_SECONDS, refresh)
        context.client.on_disconnect(lambda: poll_timer.cancel(with_current_invocation=True))
