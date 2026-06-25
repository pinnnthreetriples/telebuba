"""Neurocomment work-view rendering — board, counters, drill-down, per-pair actions.

Split out of ``_page`` to stay within the file-size budget. UI-thin, fully
``pragma: no cover``; the pure display helpers it uses live in ``_page`` (and are
unit-tested there). ``_page`` imports ``render_work_view`` lazily, so this module
can import the helpers from ``_page`` at module level without a cycle.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from nicegui import ui

from core.config import settings
from features.neurocomment._page import (
    PageContext,
    board_content_signature,
    challenge_summary,
    channel_status_icon,
    channel_status_label,
    counter_window_since,
    health_label,
    solver_switch_key,
)
from services.neurocomment import (
    count_challenge_outcomes,
    list_channel_challenges,
    retry_pair,
    set_solver_enabled,
    skip_pair,
)

if TYPE_CHECKING:
    from schemas.neurocomment import NeurocommentBoard

_CHALLENGE_DRILLDOWN_LIMIT = 10
# Header-counter time windows → RU label.
_COUNTER_WINDOWS: dict[str, str] = {"today": "Сегодня", "7d": "7 дней", "all": "Всё время"}
# Per-campaign solver switch key ↔ solver_enabled value.
_SOLVER_SWITCH: dict[str, bool | None] = {"follow": None, "on": True, "off": False}


async def render_work_view(ctx: PageContext) -> None:  # pragma: no cover
    container = ui.column().classes("w-full gap-4")
    sig = {"value": board_content_signature(ctx.board)}

    @ui.refreshable
    def render() -> None:
        _render_board(ctx.board)

    async def reload() -> None:
        # Anti-flicker: reload from ctx, re-render only when the content digest changed
        new_sig = board_content_signature(ctx.board)
        if new_sig == sig["value"]:
            return
        sig["value"] = new_sig
        render.refresh()

    ctx.on_reload_callbacks.append(reload)

    with container:
        render()


def _render_board(board: NeurocommentBoard | None) -> None:  # pragma: no cover
    if board is None:
        ui.label("Кампания не найдена").classes("text-sm text-slate-400")
        return
    _render_solver_controls(board)
    with ui.row().classes("w-full gap-4 items-start flex-wrap"):
        _render_channels_panel(board)
        _render_accounts_panel(board)


def _render_solver_controls(board: NeurocommentBoard) -> None:  # pragma: no cover
    """Minimalist «Капчи» strip: solver switch + window toggle + inline outcome counters."""
    channels = [row.channel for row in board.channels]
    global_enabled = settings.neurocomment.challenge_solver_enabled
    default_text = "ВКЛ" if global_enabled else "ВЫКЛ"
    solver_options = {
        "follow": f"По настройке ({default_text})",
        "on": "Включено",
        "off": "Выключено",
    }
    with ui.card().classes(  # noqa: SIM117
        "w-full p-2 px-3 gap-2 border border-slate-200 dark:border-zinc-800 "
        "bg-slate-50 dark:bg-zinc-900 rounded-xl shadow-sm",
    ):
        with ui.row().classes("w-full items-center justify-between gap-2 flex-wrap"):
            with ui.row().classes("items-center gap-2 flex-wrap"):
                ui.icon("vpn_key").classes("text-base text-indigo-500")
                ui.label("Решение капч").classes("text-xs font-semibold")
                help_icon = ui.icon("help_outline").classes("text-slate-400 text-sm cursor-help")
                help_icon.tooltip(
                    "Настройка автоматического решения приветственных капч ботов "
                    "в группах обсуждения каналов при онбординге или отправке комментариев."
                )
                switch = (
                    ui.select(solver_options, value=solver_switch_key(board.solver_enabled))
                    .props("dense outlined options-dense")
                    .classes("w-[170px]")
                    .style("font-size: 0.75rem;")
                )

                async def on_switch(event: object) -> None:
                    key = str(getattr(event, "value", "follow"))
                    await set_solver_enabled(board.campaign_id, _SOLVER_SWITCH[key])
                    ui.notify("Настройка солвера сохранена", type="positive")

                switch.on_value_change(on_switch)

            with ui.row().classes("items-center gap-2 flex-wrap"):
                window = (
                    ui.select(_COUNTER_WINDOWS, value="all")
                    .props("dense outlined options-dense")
                    .classes("w-[100px]")
                    .style("font-size: 0.75rem;")
                )
                counts = ui.label("").classes("text-[11px] text-slate-500 tabular-nums")
                counts.tooltip("✓ решено · ✗ не решено · ⊘ отказ · ⏳ в ожидании")

                async def reload_counts() -> None:
                    result = await count_challenge_outcomes(
                        channels, counter_window_since(window.value, datetime.now(UTC))
                    )
                    msg = (
                        f"✓ {result.solved} · ✗ {result.failed} · "
                        f"⊘ {result.give_up} · ⏳ {result.pending}"
                    )
                    counts.set_text(msg)

                window.on_value_change(lambda _e: reload_counts())
                ui.timer(0.1, reload_counts, once=True)


def _render_channels_panel(board: NeurocommentBoard) -> None:  # pragma: no cover
    with ui.card().classes(
        "w-[300px] p-2 px-3 gap-1.5 border border-slate-200 dark:border-zinc-800 "
        "bg-white dark:bg-zinc-900 rounded-xl shadow-sm",
    ):
        ui.label("Каналы").classes("text-xs font-semibold")
        if not board.channels:
            ui.label("Каналов пока нет").classes("text-xs text-slate-400")
        for row in board.channels:
            with ui.column().classes("w-full gap-0"):
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label(row.channel).classes("text-xs")
                    with ui.row().classes("items-center gap-1.5"):
                        ui.label(f"{row.ready_accounts}/{row.total_accounts}").classes(
                            "text-[10px] text-slate-500",
                        )
                        ui.icon(channel_status_icon(row.status)).classes("text-sm text-slate-500")
                        ui.badge(channel_status_label(row.status)).props("color=blue-grey").classes(
                            "text-[9px] py-0.5 px-1",
                        )
                if row.status == "bot_challenge":
                    _render_challenge_drilldown(row.channel)


def _render_challenge_drilldown(channel: str) -> None:  # pragma: no cover
    """Lazy expansion: on open, list the channel's recent failed challenges + actions.

    Loads on demand (not on every board poll) so the 4 s refresh stays cheap.
    """
    expansion = ui.expansion("Капчи бота").props("dense").classes("w-full text-[11px]")
    with expansion:
        rows_box = ui.column().classes("w-full gap-0")

    async def load() -> None:
        result = await list_channel_challenges(channel, _CHALLENGE_DRILLDOWN_LIMIT)
        rows_box.clear()
        with rows_box:
            if not result.rows:
                ui.label("Записей пока нет").classes("text-[10px] text-slate-400")
            for r in result.rows:
                with ui.row().classes("w-full items-center justify-between gap-2"):
                    ui.label(challenge_summary(r)).classes("text-[10px] text-slate-600")
                    with ui.row().classes("gap-1"):
                        ui.button(
                            icon="refresh",
                            on_click=lambda _e=None, a=r.account_id, c=r.channel: _on_retry(a, c),
                        ).props("flat dense round color=primary")
                        ui.button(
                            icon="block",
                            on_click=lambda _e=None, a=r.account_id, c=r.channel: _on_skip(a, c),
                        ).props("flat dense round color=grey-7")

    async def on_toggle(event: object) -> None:
        if getattr(event, "value", False):
            await load()

    expansion.on_value_change(on_toggle)


async def _on_retry(account_id: str, channel: str) -> None:  # pragma: no cover
    await retry_pair(account_id, channel)
    ui.notify("Пара переонбоардится", type="info")


async def _on_skip(account_id: str, channel: str) -> None:  # pragma: no cover
    await skip_pair(account_id, channel)
    ui.notify("Пара пропущена оператором", type="info")


def _render_accounts_panel(board: NeurocommentBoard) -> None:  # pragma: no cover
    with ui.column().classes("flex-1 min-w-[360px] gap-2"):
        if not board.accounts:
            ui.label("Аккаунтов в кампании пока нет").classes("text-xs text-slate-400")
        for card in board.accounts:
            _render_account_card(card)


def _render_account_card(card) -> None:  # noqa: ANN001  # pragma: no cover
    # Thin left accent by health (green ready / red blocked) — visual cohesion with
    # the engine panel + at-a-glance scanning, mirroring the warming card stripe.
    accent = "border-emerald-400" if card.health == "ready" else "border-red-300"
    with ui.card().classes(
        f"w-full p-2 px-3 gap-0.5 border border-slate-200 dark:border-zinc-800 "
        f"bg-white dark:bg-zinc-900 rounded-xl shadow-sm border-l-4 {accent}",
    ):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label(card.label).classes("text-xs font-semibold")
            ui.badge(health_label(card.health)).props(
                f"color={'green' if card.health == 'ready' else 'red'}",
            ).classes("text-[9px] py-0.5 px-1")
        ui.label(
            f"Комментариев за час: {card.comments_last_hour}/{card.max_comments_per_hour} · "
            f"за сутки: {card.comments_today}",
        ).classes("text-[10px] text-slate-600")
        ui.label(
            f"Доверие: {card.trust_score} ({card.trust_band})"
            + (f" · spam: {card.spam_status}" if card.spam_status else ""),
        ).classes("text-[10px] text-slate-500")
        if card.last_comment_at:
            stamp = card.last_comment_at[:16].replace("T", " ")
            ui.label(f"Последний комментарий: {stamp}").classes("text-[9px] text-slate-400")
