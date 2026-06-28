"""Warming board page header — H1, the three counters, and the pool toggle.

Extracted from ``_board`` to keep that module under the aislop file-length
cap. UI-thin per non-negotiable #1; every function is exercised manually and
excluded from coverage. The master toggle reuses the existing per-account
start/stop services across the relevant set — no new runtime wiring — so the
kanban drag-drop semantics stay untouched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import ui

if TYPE_CHECKING:
    from features.warming._board import _BoardContext
    from schemas.warming import WarmingBoardState


def _render_page_header(
    board: WarmingBoardState,
    ctx: _BoardContext,
) -> None:  # pragma: no cover
    """Spec C.3 page header: H1 + three counters + the master pool toggle.

    Counters: «в прогреве» (#0066FF) = active warming column size, «готовы»
    (#0B0B0C) = ready idle accounts, «ошибки» (#C0473F). The master toggle
    reuses the existing per-account start/stop services across the relevant
    set — no new runtime wiring — so the kanban drag-drop semantics are
    untouched. Running = any account in the warming column.
    """
    running = board.active_count > 0 or bool(board.warming)
    errors = sum(1 for card in (*board.idle, *board.warming) if card.state == "error")
    ready = board.summary.ready
    with ui.row().classes("w-full items-center justify-between flex-wrap gap-3"):
        ui.label("Прогрев аккаунтов").classes("tb-h1")
        with ui.row().classes("items-center gap-[18px]"):
            _counter(str(len(board.warming)), "в прогреве", "tbw-text-blue")
            _counter(str(ready), "готовы", "")
            _counter(str(errors), "ошибки", "tbw-text-red")
            _render_master_toggle(board, ctx, running=running)


def _counter(value: str, label: str, color_cls: str) -> None:  # pragma: no cover
    """One right-aligned header counter: big value (19px/700) + muted label."""
    with ui.column().classes("items-end gap-0"):
        ui.label(value).classes(
            f"text-[19px] font-bold leading-none tabular-nums {color_cls or 'text-[#0B0B0C]'}",
        )
        ui.label(label).classes("text-[11px] text-[#74726E]")


def _render_master_toggle(
    board: WarmingBoardState,
    ctx: _BoardContext,
    *,
    running: bool,
) -> None:  # pragma: no cover
    """«Остановить пул» / «Запустить пул» — fleet start/stop over existing services."""
    import contextlib  # noqa: PLC0415

    from schemas.warming import StartWarmingRequest, StopWarmingRequest  # noqa: PLC0415
    from services.warming import start_warming, stop_warming  # noqa: PLC0415

    async def on_toggle() -> None:
        if running:
            for card in list(board.warming):
                # Best-effort fleet stop — a per-card failure must not abort the
                # rest, and per-card toasts would spam; the drag path keeps the
                # detailed single-account error reporting.
                with contextlib.suppress(Exception):
                    await stop_warming(StopWarmingRequest(account_id=card.account_id))
            ui.notify("Пул остановлен", type="info")
        else:
            started = 0
            for card in list(board.idle):
                if card.readiness is not None and not card.readiness.ready:
                    continue
                # Unready accounts (or a transient start failure) are skipped
                # silently — the drag path is where an operator gets per-account
                # blockers; the pool button is a coarse "start everything ready".
                with contextlib.suppress(Exception):
                    await start_warming(StartWarmingRequest(account_id=card.account_id))
                    started += 1
            ui.notify(
                f"Пул запущен · аккаунтов: {started}" if started else "Нет готовых аккаунтов",
                type="positive" if started else "warning",
            )
        ctx.refresh()

    label = "Остановить пул" if running else "Запустить пул"
    icon = "pause" if running else "play_arrow"
    tone = "tb-btn-dark" if running else "tb-btn-primary"
    ui.button(label, icon=icon, on_click=on_toggle).props("unelevated no-caps").classes(
        f"tb-btn {tone}",
    )
