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

from nicegui import context, ui

from features.neurocomment._page import (
    PIPELINE_STEPS,
    FleetActivity,
    fleet_activity,
    relative_time,
    runtime_status_text,
)
from services.accounts import list_accounts
from services.neurocomment import (
    load_neurocomment_board,
    neurocomment_runtime_status,
    start_neurocomment,
    stop_neurocomment,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from schemas.neurocomment import NeurocommentRuntimeStatus

_PANEL_POLL_SECONDS = 4.0
_ZERO_ACTIVITY = FleetActivity(0, 0, 0, 0, 0, 0)


@dataclasses.dataclass
class _PanelState:
    """The panel's render inputs, refreshed in place on each poll."""

    status: NeurocommentRuntimeStatus
    activity: FleetActivity
    last_comment: str | None
    flash_today: bool = False


async def render_engine_panel(campaign_id: str) -> None:  # pragma: no cover
    """Render the engine hero card with the live rail, counters, and controls."""
    accounts = (await list_accounts()).accounts
    listener_options = {acc.account_id: (acc.label or acc.account_id) for acc in accounts}
    state = await _load_state(campaign_id)

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

        rail_section()
        live_section()

        async def reload() -> None:
            # Compare the fresh figures against the still-current state, then adopt them
            # wholesale (the refreshables re-read ``state`` on refresh).
            nonlocal state
            fresh = await _load_state(campaign_id)
            flipped = fresh.status.running != state.status.running
            fresh.flash_today = fresh.activity.comments_today > state.activity.comments_today
            state = fresh
            live_section.refresh()
            if flipped:
                rail_section.refresh()

        ui.separator()
        _render_controls(listener_options, state.status.listener_account_id, reload)

    timer = ui.timer(_PANEL_POLL_SECONDS, reload)
    context.client.on_disconnect(lambda: timer.cancel(with_current_invocation=True))


def _render_controls(
    listener_options: dict[str, str],
    initial_listener: str | None,
    reload: Callable[[], Awaitable[None]],
) -> None:  # pragma: no cover
    """Listener select + Start/Stop; both buttons refresh the panel via ``reload``."""
    listener_select = (
        ui.select(listener_options, label="Аккаунт-слушатель")
        .props("dense outlined")
        .classes("w-full max-w-[400px]")
    )
    listener_select.value = initial_listener

    async def on_start() -> None:
        if not listener_select.value:
            ui.notify("Выберите аккаунт-слушатель", type="warning")
            return
        await start_neurocomment(listener_select.value)
        ui.notify("Нейрокомментинг запущен", type="positive")
        await reload()

    async def on_stop() -> None:
        await stop_neurocomment()
        ui.notify("Нейрокомментинг остановлен", type="info")
        await reload()

    with ui.row().classes("w-full items-center gap-2"):
        ui.button("Запустить", icon="play_arrow", on_click=on_start).props("color=positive")
        ui.button("Остановить", icon="stop", on_click=on_stop).props("color=negative outline")
    ui.label(
        "Один слушатель на все активные кампании; движок раздаёт посты по их "
        "кампаниям. «Остановить» останавливает весь флот.",
    ).classes("text-xs text-slate-500")


async def _load_state(campaign_id: str) -> _PanelState:  # pragma: no cover
    """Fetch runtime status + the board and reduce them to the panel's render inputs.

    ponytail: this re-loads the same board the work view polls (≈11 bulk reads each,
    4 s apart) — a deliberate doubling on a single-operator local SQLite page. Share
    one timer/board between the two panels only if it ever shows up as a real cost.
    """
    status = await neurocomment_runtime_status()
    board = await load_neurocomment_board(campaign_id)
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
    rail_cls = "tb-nc-rail w-full items-start gap-0" + (" tb-nc-on" if running else "")
    with ui.row().classes(rail_cls):
        for idx, step in enumerate(PIPELINE_STEPS):
            if idx > 0:
                ui.element("div").classes("tb-nc-conn flex-1").style("margin-top: 20px")
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
            ui.label("Движок остановлен — выберите слушателя и нажмите «Запустить».").classes(
                "text-xs text-slate-500",
            )


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
