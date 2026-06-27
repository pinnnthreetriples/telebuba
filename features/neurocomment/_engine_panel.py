"""Neurocomment engine panel — the animated hero card (page redesign).

The centerpiece of the redesigned page: a six-step pipeline rail that mirrors the
engine flow, a live status ticker, fleet activity counters, and the Start/Stop
controls. While the engine runs the rail animates (flowing connectors + a wave of
node pulses) so the operator *sees* the process is live; on Start the rail re-mounts
and the nodes play a one-shot launch cascade (pure CSS, see ``__init__._NC_CSS``).

UI-thin per non-negotiable #1 — every function carries ``# pragma: no cover``. It
reads only ``services.neurocomment`` (runtime status + the board read model) and the
pure label/aggregation helpers from ``_page`` (which are unit-tested there). ``_page``
imports ``render_engine_panel`` lazily, so this module can import those helpers at
module level without a cycle (same pattern as ``_workview``).
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from nicegui import ui

from features.neurocomment._logpanel import LogPanelState, refresh_logs, render_log_panel
from features.neurocomment._page import (
    PIPELINE_STEPS,
    FleetActivity,
    PageContext,
    count_ready_accounts_across_active_campaigns,
    fleet_activity,
    live_signature,
    relative_time,
    runtime_status_text,
    start_block_reason,
)
from schemas.neurocomment import NeurocommentRuntimeStatus
from services.accounts import list_listener_accounts
from services.neurocomment import (
    start_neurocomment,
    stop_neurocomment,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

_ZERO_ACTIVITY = FleetActivity(0, 0, 0, 0, 0, 0)


@dataclasses.dataclass
class _PanelState:
    """The panel's render inputs, refreshed in place on each poll."""

    status: NeurocommentRuntimeStatus
    activity: FleetActivity
    last_comment: str | None
    flash_today: bool = False


async def render_engine_panel(ctx: PageContext) -> None:  # pragma: no cover
    """Render the engine hero card with the live rail, counters, and controls."""
    accounts = (await list_listener_accounts()).accounts
    listener_options = {acc.account_id: (acc.label or acc.account_id) for acc in accounts}
    state = _load_state_from_ctx(ctx)

    with ui.card().classes("w-full p-5 gap-4"):
        with ui.row().classes("w-full items-center justify-between"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("smart_toy").classes("text-indigo-500")
                ui.label("Движок нейрокомментинга").classes("text-base font-semibold")
            status_pill = ui.row().classes("items-center gap-2")

        @ui.refreshable
        def rail_section() -> None:
            _render_rail(running=state.status.running)

        @ui.refreshable
        def live_section() -> None:
            _render_status_pill(status_pill, state.status)
            _render_ticker(state)
            _render_counters(state)

        log_state = LogPanelState()

        @ui.refreshable
        def log_section() -> None:
            render_log_panel(log_state, on_toggle_log)

        async def on_toggle_log() -> None:
            log_state.expanded = not log_state.expanded
            if log_state.expanded:
                await refresh_logs(log_state)
            log_section.refresh()

        rail_section()
        live_section()

        async def reload() -> None:
            # Compare the fresh figures against the still-current state, then adopt them
            # wholesale (the refreshables re-read ``state`` on refresh). Anti-flicker:
            # refresh the live section only when its digest changed (no 4 s blink).
            nonlocal state
            fresh = _load_state_from_ctx(ctx)
            flipped = fresh.status.running != state.status.running
            fresh.flash_today = fresh.activity.comments_today > state.activity.comments_today
            live_changed = live_signature(
                fresh.status, fresh.activity, fresh.last_comment
            ) != live_signature(state.status, state.activity, state.last_comment)
            state = fresh
            if live_changed:
                live_section.refresh()
            if flipped:
                rail_section.refresh()
            # Refresh the open log panel only when a new neurocomment row landed.
            if log_state.expanded and await refresh_logs(log_state):
                log_section.refresh()

        ctx.on_reload_callbacks.append(reload)

        ui.separator()
        _render_controls(
            listener_options,
            state.status.listener_account_id,
            reload,
            count_ready_accounts_across_active_campaigns,
        )
        log_section()


def _render_controls(
    listener_options: dict[str, str],
    initial_listener: str | None,
    reload: Callable[[], Awaitable[None]],
    ready_accounts: Callable[[], Awaitable[int]],
) -> None:  # pragma: no cover
    """Listener select + Start/Stop; both buttons refresh the panel via ``reload``.

    Start is gated by ``start_block_reason`` so it can't silently no-op on an empty
    fleet; Stop is fleet-wide, so it asks for confirmation first.
    """
    listener_select = (
        ui.select(listener_options, label="Аккаунт-слушатель")
        .props("dense outlined")
        .classes("w-full max-w-[400px]")
    )
    # Only preselect the persisted listener if it still has a live session; a stale id
    # (account signed out since it was set) would otherwise pass the has-listener gate
    # and start the engine on a dead listener.
    listener_select.value = initial_listener if initial_listener in listener_options else None
    if not listener_options:
        ui.label(
            "Нет аккаунтов с активной сессией — добавьте и проверьте их на «Аккаунтах».",
        ).classes("text-xs text-amber-600")

    async def on_start() -> None:
        ready_cnt = await ready_accounts()
        reason = start_block_reason(ready_cnt, has_listener=bool(listener_select.value))
        if reason:
            ui.notify(reason, type="warning")
            return
        # Start runs onboarding for active campaigns inside the call (multi-minute
        # work). Disable both buttons + show a loading spinner so the operator can't
        # double-click; surface progress as transient toasts via on_progress.
        ui.notify("Подготовка и запуск нейрокомментинга…", type="info")
        start_btn.props("loading")
        start_btn.props(add="disable")
        stop_btn.props(add="disable")
        try:
            await start_neurocomment(
                listener_select.value,
                on_progress=lambda msg: ui.notify(msg[:80], type="info", position="bottom"),
            )
            ui.notify("Нейрокомментинг запущен", type="positive")
        except Exception as exc:
            ui.notify(f"Старт прерван: {type(exc).__name__}", type="negative")
            raise
        finally:
            start_btn.props(remove="loading disable")
            stop_btn.props(remove="disable")
            await reload()

    async def on_stop() -> None:
        with ui.dialog() as dialog, ui.column().classes("bg-white p-4 gap-3 w-[420px] max-w-full"):
            ui.label("Остановить весь флот?").classes("text-base font-semibold")
            ui.label(
                "Слушатель остановится для всех активных кампаний.",
            ).classes("text-sm text-slate-700")

            async def confirm() -> None:
                dialog.close()
                await stop_neurocomment()
                ui.notify("Нейрокомментинг остановлен", type="info")
                await reload()

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Отмена", color="grey-7", on_click=dialog.close).props("flat")
                ui.button("Остановить", color="negative", on_click=confirm)
        dialog.open()

    with ui.row().classes("w-full items-center gap-2"):
        start_btn = ui.button("Запустить", icon="play_arrow", on_click=on_start).props(
            "color=positive",
        )
        stop_btn = ui.button("Остановить", icon="stop", on_click=on_stop).props(
            "color=negative outline",
        )
    ui.label(
        "Один слушатель на все активные кампании; движок раздаёт посты по их "
        "кампаниям. «Остановить» останавливает весь флот.",
    ).classes("text-xs text-slate-500")


def _load_state_from_ctx(ctx: PageContext) -> _PanelState:  # pragma: no cover
    """Reduce pre-loaded status + board from PageContext to the panel's render inputs."""
    status = ctx.status if ctx.status is not None else NeurocommentRuntimeStatus(running=False)
    board = ctx.board
    activity = fleet_activity(board) if board is not None else _ZERO_ACTIVITY
    last_iso = (
        max((c.last_comment_at for c in board.accounts if c.last_comment_at), default=None)
        if board is not None
        else None
    )
    return _PanelState(
        status=status,
        activity=activity,
        last_comment=relative_time(last_iso, datetime.now(UTC)),
    )


def _render_rail(*, running: bool) -> None:  # pragma: no cover
    """The six-step pipeline rail; ``tb-nc-on`` switches it from calm to animated."""
    rail_cls = (
        "tb-nc-rail w-full flex flex-col md:flex-row items-center md:items-start "
        "gap-1 md:gap-0" + (" tb-nc-on" if running else "")
    )
    with ui.element("div").classes(rail_cls):
        for idx, step in enumerate(PIPELINE_STEPS):
            if idx > 0:
                ui.element("div").classes("tb-nc-conn")
            with ui.column().classes("items-center gap-1 shrink-0"):
                circle_cls = (
                    "bg-indigo-600 text-white shadow" if running else "bg-slate-100 text-slate-400"
                )
                circle = ui.element("div").classes(
                    f"tb-nc-node w-10 h-10 rounded-full flex items-center "
                    f"justify-center shrink-0 {circle_cls}",
                )
                circle.style(f"--i: {idx}")
                with circle:
                    ui.icon(step.icon).classes("text-lg")
                circle.tooltip(step.detail)
                label_cls = "text-indigo-700 font-medium" if running else "text-slate-400"
                ui.label(step.label).classes(f"text-[10px] leading-none {label_cls}")


def _render_status_pill(
    container: ui.row,
    status: NeurocommentRuntimeStatus,
) -> None:  # pragma: no cover
    """The header pill: a blinking green dot + label when running, grey when stopped."""
    container.clear()
    with container:
        dot = "w-2 h-2 rounded-full "
        dot += "bg-emerald-500 tb-nc-dot" if status.running else "bg-slate-300"
        ui.element("div").classes(dot)
        text_cls = "text-emerald-700" if status.running else "text-slate-500"
        ui.label(runtime_status_text(status)).classes(f"text-xs font-medium {text_cls}")


def _render_ticker(state: _PanelState) -> None:  # pragma: no cover
    """One live line under the rail: what the engine is doing right now."""
    with ui.row().classes("w-full items-center gap-2"):
        if state.status.running:
            ui.element("div").classes("w-2 h-2 rounded-full bg-indigo-500 tb-nc-dot")
            tail = (
                f"последний коммент {state.last_comment}"
                if state.last_comment
                else "комментариев ещё не было"
            )
            ui.label(f"{runtime_status_text(state.status)} · {tail}").classes(
                "text-xs text-indigo-700",
            )
        else:
            ui.element("div").classes("w-2 h-2 rounded-full bg-slate-300")
            idle_msg = (
                "Нет готовых аккаунтов — добавьте и онбордните их в «Настройке»."
                if state.activity.ready_accounts == 0
                else "Движок остановлен — выберите слушателя и нажмите «Запустить»."
            )
            ui.label(idle_msg).classes("text-xs text-slate-500")


def _render_counters(state: _PanelState) -> None:  # pragma: no cover
    """Four compact fleet-activity stats; «сегодня» flashes when it ticks up."""
    activity = state.activity
    with ui.row().classes("w-full gap-2 flex-wrap"):
        _stat(str(activity.comments_last_hour), "комментариев за час", accent="text-indigo-700")
        _stat(
            str(activity.comments_today),
            "за сегодня",
            accent="text-emerald-700",
            flash=state.flash_today,
        )
        _stat(
            f"{activity.ready_accounts}/{activity.total_accounts}",
            "готовых аккаунтов",
            accent="text-slate-700",
        )
        _stat(
            f"{activity.ready_channels}/{activity.total_channels}",
            "каналов готово",
            accent="text-slate-700",
        )


def _stat(value: str, label: str, *, accent: str, flash: bool = False) -> None:  # pragma: no cover
    box_cls = "flex-1 min-w-[120px] rounded-lg border border-slate-100 bg-slate-50 px-3 py-2 gap-0"
    if flash:
        box_cls += " tb-nc-flash"
    with ui.column().classes(box_cls):
        ui.label(value).classes(f"text-xl font-semibold leading-tight {accent}")
        ui.label(label).classes("text-[11px] text-slate-500 leading-tight")
