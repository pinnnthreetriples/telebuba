"""Neurocomment engine panel — the animated pipeline hero card (design spec C.4).

The centerpiece of the page: a six-node pipeline rail that mirrors the engine flow
(Новый пост · Выбор аккаунта · Генерация · Публикация · Проверка · Готово), a status
badge + master toggle, a live active-description strip, and a four-cell stat grid.
While the engine runs the rail animates (flowing connectors + a wave of node pulses)
so the operator *sees* the process is live; on Start the rail re-mounts and the nodes
play a one-shot launch cascade (pure CSS, see ``__init__._NC_CSS``).

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
    board_captcha_count,
    board_error_count,
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

# Active-step accent + done-state accent (design palette).
_BLUE = "#0066FF"
_GREEN = "#12A150"
_RED = "#E5372A"
_AMBER = "#E08700"
_TRACK = "#DCE2EC"


@dataclasses.dataclass
class _PanelState:
    """The panel's render inputs, refreshed in place on each poll."""

    status: NeurocommentRuntimeStatus
    activity: FleetActivity
    last_comment: str | None
    captcha: int = 0
    errors: int = 0
    flash_today: bool = False


async def render_engine_panel(ctx: PageContext) -> None:  # pragma: no cover
    """Render the pipeline hero card with the live rail, counters, and controls."""
    accounts = (await list_listener_accounts()).accounts
    listener_options = {acc.account_id: (acc.label or acc.account_id) for acc in accounts}
    state = _load_state_from_ctx(ctx)

    with ui.element("div").classes("tb-card-blue w-full").style("padding:16px 18px"):
        with (
            ui.row()
            .classes("w-full items-center justify-between flex-nowrap")
            .style(
                "margin-bottom:14px",
            )
        ):
            with ui.row().classes("items-center gap-2 flex-nowrap"):
                ui.label("Конвейер обработки постов").classes("tb-title-lg")
                status_badge = ui.row().classes("items-center")
            controls_box = ui.row().classes("items-center")

        @ui.refreshable
        def rail_section() -> None:
            _render_rail(running=state.status.running)

        @ui.refreshable
        def live_section() -> None:
            _render_status_badge(status_badge, state.status)
            _render_active_strip(state)
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
            live_changed = _live_digest(fresh) != _live_digest(state)
            state = fresh
            if live_changed:
                live_section.refresh()
            if flipped:
                rail_section.refresh()
                # Master toggle label/style only depends on running — repaint on flip.
                _render_controls_button(controls_box, state.status.running, on_start, on_stop)
            # Refresh the open log panel only when a new neurocomment row landed.
            if log_state.expanded and await refresh_logs(log_state):
                log_section.refresh()

        ctx.on_reload_callbacks.append(reload)

        on_start, on_stop = _make_handlers(
            listener_options,
            state.status.listener_account_id,
            reload,
            count_ready_accounts_across_active_campaigns,
        )
        _render_controls_button(controls_box, state.status.running, on_start, on_stop)
        log_section()


def _live_digest(state: _PanelState) -> tuple[object, ...]:  # pragma: no cover
    """Digest of everything the live section paints — adds captcha/error to the base."""
    return (
        *live_signature(state.status, state.activity, state.last_comment),
        state.captcha,
        state.errors,
    )


# Controls: master toggle + listener select + Start/Stop logic.


def _render_controls_button(
    container: ui.row,
    running: bool,  # noqa: FBT001
    on_start: Callable[[], Awaitable[None]],
    on_stop: Callable[[], Awaitable[None]],
) -> None:  # pragma: no cover
    """The master toggle in the hero header: «Остановить» (white) / «Запустить» (blue)."""
    container.clear()
    with container:
        if running:
            btn = ui.button("Остановить", on_click=on_stop)
            btn.props("flat no-caps").classes("tb-btn tb-btn-white")
            icon = "stop"
        else:
            btn = ui.button("Запустить", on_click=on_start)
            btn.props("flat no-caps").classes("tb-btn tb-btn-primary")
            icon = "play_arrow"
        # Leading pause/play glyph inside the pill (Quasar icon slot would re-add caps).
        with btn:
            ui.icon(icon).classes("text-base").style("margin-right:-2px;order:-1")


def _make_handlers(
    listener_options: dict[str, str],
    initial_listener: str | None,
    reload: Callable[[], Awaitable[None]],
    ready_accounts: Callable[[], Awaitable[int]],
) -> tuple[Callable[[], Awaitable[None]], Callable[[], Awaitable[None]]]:  # pragma: no cover
    """Build the Start/Stop coroutines + render the listener picker beneath the rail.

    Start is gated by ``start_block_reason`` so it can't silently no-op on an empty
    fleet; Stop is fleet-wide, so it asks for confirmation first. The listener select
    lives inside the hero card (the spec's standalone listener sidebar card is the
    one acknowledged layout deviation — the select stays wired to Start/Stop here).
    """
    with (
        ui.column()
        .classes("w-full gap-1")
        .style(
            "margin-top:12px;padding-top:12px;border-top:1px solid #E4ECFA",
        )
    ):
        ui.label("Аккаунт-слушатель").classes("tb-uplabel")
        listener_select = (
            ui.select(listener_options).props("dense outlined options-dense").classes("w-full")
        )
        # Only preselect the persisted listener if it still has a live session; a stale
        # id (signed out since it was set) would pass the has-listener gate and start
        # the engine on a dead listener.
        listener_select.value = initial_listener if initial_listener in listener_options else None
        if not listener_options:
            ui.label(
                "Нет аккаунтов с активной сессией — добавьте и проверьте их на «Аккаунтах».",
            ).style("font-size:11px;color:#E08700")

    async def on_start() -> None:
        ready_cnt = await ready_accounts()
        reason = start_block_reason(ready_cnt, has_listener=bool(listener_select.value))
        if reason:
            ui.notify(reason, type="warning")
            return
        # Start runs onboarding for active campaigns inside the call (multi-minute
        # work). Surface progress as transient toasts via on_progress.
        ui.notify("Подготовка и запуск нейрокомментинга…", type="info")
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
            await reload()

    async def on_stop() -> None:
        with (
            ui.dialog() as dialog,
            ui.card().classes("p-0 w-[420px] max-w-full").style("border-radius:18px"),
            ui.column().classes("w-full gap-3").style("padding:20px"),
        ):
            ui.label("Остановить весь флот?").classes("tb-title-lg")
            ui.label(
                "Слушатель остановится для всех активных кампаний.",
            ).classes("tb-muted").style("line-height:1.5")

            async def confirm() -> None:
                dialog.close()
                await stop_neurocomment()
                ui.notify("Нейрокомментинг остановлен", type="info")
                await reload()

            with ui.row().classes("w-full justify-end gap-2").style("margin-top:4px"):
                cancel = ui.button("Отмена", on_click=dialog.close)
                cancel.props("flat no-caps").classes("tb-btn tb-btn-white")
                stop = ui.button("Остановить", on_click=confirm)
                stop.props("flat no-caps").classes("tb-btn tb-btn-danger")
        dialog.open()

    return on_start, on_stop


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
        captcha=board_captcha_count(board),
        errors=board_error_count(board),
    )


# Rail / badge / strip / counters.


def _render_rail(*, running: bool) -> None:  # pragma: no cover
    """The six-node pipeline rail; ``tb-nc-on`` switches it from calm to animated."""
    rail_cls = (
        "tb-nc-rail w-full flex flex-col md:flex-row items-center md:items-start "
        "gap-1 md:gap-0" + (" tb-nc-on" if running else "")
    )
    with ui.element("div").classes(rail_cls).style("margin:0 8px 12px"):
        for idx, step in enumerate(PIPELINE_STEPS):
            if idx > 0:
                ui.element("div").classes("tb-nc-conn")
            with ui.column().classes("items-center gap-1 shrink-0").style("width:88px"):
                node = ui.element("div").classes(
                    "tb-nc-node flex items-center justify-center shrink-0 tb-livedot"
                    if running
                    else "tb-nc-node flex items-center justify-center shrink-0",
                )
                node.style(_node_style(running=running) + f";--i:{idx}")
                with node:
                    if running:
                        ui.icon(step.icon).style("font-size:15px;color:#fff")
                node.tooltip(step.detail)
                label_color = _BLUE if running else "#A8A6A1"
                label_weight = "600" if running else "400"
                ui.label(step.label).style(
                    f"font-size:11px;text-align:center;line-height:1.2;"
                    f"color:{label_color};font-weight:{label_weight}",
                )


def _node_style(*, running: bool) -> str:  # pragma: no cover
    if running:
        return (
            "width:16px;height:16px;border-radius:50%;background:#0066FF;"
            "box-shadow:0 0 0 0 rgba(0,102,255,.55)"
        )
    return (
        "width:9px;height:9px;border-radius:50%;background:#fff;"
        "border:1.5px solid #C9D2E0;margin:3.5px 0"
    )


def _render_status_badge(
    container: ui.row,
    status: NeurocommentRuntimeStatus,
) -> None:  # pragma: no cover
    """Header badge: pulsing green «работает» when running, amber «остановлен» when not."""
    container.clear()
    with container:
        if status.running:
            bg, color, label, pulse = "#DDF7E9", _GREEN, "работает", " tb-pulse"
        else:
            bg, color, label, pulse = "#FFF3D1", "#B8860B", "остановлен", ""
        ui.html(
            f'<span class="tb-badge{pulse}" style="background:{bg};color:{color};'
            f'font-size:11px;font-weight:600">'
            f'<span class="tb-badge-dot"></span>{label}</span>',
        )


_DOT_BASE = "width:8px;height:8px;border-radius:50%;flex-shrink:0"


def _render_active_strip(state: _PanelState) -> None:  # pragma: no cover
    """The blue active-description strip under the rail — what the engine does now."""
    if state.status.running:
        tail = (
            f"последний коммент {state.last_comment}"
            if state.last_comment
            else "комментариев ещё не было"
        )
        text = f"{runtime_status_text(state.status)} · {tail}"
        dot = f'<span class="pl-pulse" style="{_DOT_BASE};background:#0066FF"></span>'
        text_cls = "tb-pulse"
        text_color = _BLUE
    else:
        text = (
            "Нет готовых аккаунтов — добавьте и онбордните их в «Настройке»."
            if state.activity.ready_accounts == 0
            else "Движок остановлен — выберите слушателя и нажмите «Запустить»."
        )
        dot = f'<span style="{_DOT_BASE};background:#C9D2E0"></span>'
        text_cls = ""
        text_color = "#74726E"
    ui.html(
        '<div style="display:flex;align-items:center;gap:9px;background:#EEF4FF;'
        'border:1px solid #DCE7FB;border-radius:10px;padding:10px 13px;margin-bottom:12px">'
        f"{dot}"
        f'<span class="{text_cls}" style="font-size:12.5px;font-weight:500;color:{text_color}">'
        f"{text}</span></div>",
    )


def _render_counters(state: _PanelState) -> None:  # pragma: no cover
    """Four-cell stat grid: accounts-in-work / comments-today / errors / captchas."""
    activity = state.activity
    flash = " tb-nc-flash" if state.flash_today else ""
    cells = (
        (str(activity.ready_accounts), "аккаунтов в работе", _BLUE, ""),
        (str(activity.comments_today), "комментариев сегодня", _GREEN, flash),
        (str(state.errors), "ошибок", _RED, ""),
        (str(state.captcha), "капчи", _AMBER, ""),
    )
    grid = ui.element("div").style(
        "display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:#E4ECFA;"
        "border:1px solid #E4ECFA;border-radius:12px;overflow:hidden;width:100%",
    )
    with grid:
        for value, label, color, extra in cells:
            cell = (
                ui.element("div")
                .classes(extra.strip())
                .style(
                    "background:#fff;padding:14px 16px",
                )
            )
            with cell:
                ui.label(value).style(
                    f"font-size:20px;font-weight:700;line-height:1.1;color:{color};"
                    "font-variant-numeric:tabular-nums",
                )
                ui.label(label).style("font-size:11px;color:#9A9893")
