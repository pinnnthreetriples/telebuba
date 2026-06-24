"""Neurocomment work-view rendering — board, counters, drill-down, per-pair actions.

Split out of ``_page`` to stay within the file-size budget. UI-thin, fully
``pragma: no cover``; the pure display helpers it uses live in ``_page`` (and are
unit-tested there). ``_page`` imports ``render_work_view`` lazily, so this module
can import the helpers from ``_page`` at module level without a cycle.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from nicegui import context, ui

from features.neurocomment._page import (
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
    load_neurocomment_board,
    retry_pair,
    set_solver_enabled,
    skip_pair,
)

if TYPE_CHECKING:
    from schemas.neurocomment import NeurocommentBoard

_BOARD_POLL_SECONDS = 4.0
# Failed-challenge rows shown in a channel's drill-down (Ф2 #145).
_CHALLENGE_DRILLDOWN_LIMIT = 10
# Header-counter time windows → RU label.
_COUNTER_WINDOWS: dict[str, str] = {"today": "Сегодня", "7d": "7 дней", "all": "Всё время"}
# Per-campaign solver switch key ↔ solver_enabled value.
_SOLVER_SWITCH: dict[str, bool | None] = {"follow": None, "on": True, "off": False}
_SOLVER_SWITCH_RU: dict[str, str] = {
    "follow": "Капчи: по настройке",
    "on": "Капчи: ВКЛ",
    "off": "Капчи: ВЫКЛ",
}


async def render_work_view(campaign_id: str) -> None:  # pragma: no cover
    board = await load_neurocomment_board(campaign_id)
    container = ui.column().classes("w-full gap-4")

    @ui.refreshable
    def render() -> None:
        _render_board(board)

    async def reload() -> None:
        nonlocal board
        board = await load_neurocomment_board(campaign_id)
        render.refresh()

    with container:
        render()
    board_timer = ui.timer(_BOARD_POLL_SECONDS, reload)
    context.client.on_disconnect(lambda: board_timer.cancel(with_current_invocation=True))


def _render_board(board: NeurocommentBoard | None) -> None:  # pragma: no cover
    if board is None:
        ui.label("Кампания не найдена").classes("text-sm text-slate-400")
        return
    _render_solver_controls(board)
    with ui.row().classes("w-full gap-4 items-start flex-wrap"):
        _render_channels_panel(board)
        _render_accounts_panel(board)


def _render_solver_controls(board: NeurocommentBoard) -> None:  # pragma: no cover
    """Per-campaign solver switch + the four outcome counters with a window toggle."""
    channels = [row.channel for row in board.channels]
    with ui.card().classes("w-full p-3 gap-2"):
        with ui.row().classes("w-full items-center gap-3"):
            switch = (
                ui.select(_SOLVER_SWITCH_RU, value=solver_switch_key(board.solver_enabled))
                .props("dense outlined")
                .classes("max-w-[220px]")
            )

            async def on_switch(event: object) -> None:
                key = str(getattr(event, "value", "follow"))
                await set_solver_enabled(board.campaign_id, _SOLVER_SWITCH[key])
                ui.notify("Настройка солвера сохранена", type="positive")

            switch.on_value_change(on_switch)
            window = (
                ui.select(_COUNTER_WINDOWS, value="all")
                .props("dense outlined")
                .classes("max-w-[160px]")
            )
        counts_box = ui.row().classes("gap-2")

        async def reload_counts() -> None:
            counts = await count_challenge_outcomes(
                channels, counter_window_since(window.value, datetime.now(UTC))
            )
            counts_box.clear()
            with counts_box:
                for label, value in (
                    ("solved", counts.solved),
                    ("failed", counts.failed),
                    ("give_up", counts.give_up),
                    ("pending", counts.pending),
                ):
                    ui.badge(f"{label}: {value}").props("color=blue-grey")

        window.on_value_change(lambda _e: reload_counts())
        ui.timer(0.1, reload_counts, once=True)


def _render_channels_panel(board: NeurocommentBoard) -> None:  # pragma: no cover
    with ui.card().classes("w-[360px] p-4 gap-2"):
        ui.label("Каналы").classes("text-base font-semibold")
        if not board.channels:
            ui.label("Каналов пока нет").classes("text-xs text-slate-400")
        for row in board.channels:
            with ui.column().classes("w-full gap-0"):
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label(row.channel).classes("text-sm")
                    with ui.row().classes("items-center gap-2"):
                        ui.label(f"{row.ready_accounts}/{row.total_accounts}").classes(
                            "text-xs text-slate-500",
                        )
                        ui.icon(channel_status_icon(row.status)).classes("text-base text-slate-500")
                        ui.badge(channel_status_label(row.status)).props("color=blue-grey")
                if row.status == "bot_challenge":
                    _render_challenge_drilldown(row.channel)


def _render_challenge_drilldown(channel: str) -> None:  # pragma: no cover
    """Lazy expansion: on open, list the channel's recent failed challenges + actions.

    Loads on demand (not on every board poll) so the 4 s refresh stays cheap.
    """
    expansion = ui.expansion("Капчи бота").props("dense").classes("w-full text-xs")
    with expansion:
        rows_box = ui.column().classes("w-full gap-0")

    async def load() -> None:
        result = await list_channel_challenges(channel, _CHALLENGE_DRILLDOWN_LIMIT)
        rows_box.clear()
        with rows_box:
            if not result.rows:
                ui.label("Записей пока нет").classes("text-xs text-slate-400")
            for r in result.rows:
                with ui.row().classes("w-full items-center justify-between gap-2"):
                    ui.label(challenge_summary(r)).classes("text-xs text-slate-600")
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
    with ui.column().classes("flex-1 min-w-[360px] gap-3"):
        if not board.accounts:
            ui.label("Аккаунтов в кампании пока нет").classes("text-xs text-slate-400")
        for card in board.accounts:
            _render_account_card(card)


def _render_account_card(card) -> None:  # noqa: ANN001  # pragma: no cover
    with ui.card().classes("w-full p-4 gap-2"):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label(card.label).classes("text-sm font-semibold")
            ui.badge(health_label(card.health)).props(
                f"color={'green' if card.health == 'ready' else 'red'}",
            )
        ui.label(
            f"Комментариев за час: {card.comments_last_hour}/{card.max_comments_per_hour} · "
            f"за сутки: {card.comments_today}",
        ).classes("text-xs text-slate-600")
        ui.label(
            f"Доверие: {card.trust_score} ({card.trust_band})"
            + (f" · спам: {card.spam_status}" if card.spam_status else ""),
        ).classes("text-xs text-slate-500")
        if card.last_comment_at:
            stamp = card.last_comment_at[:16].replace("T", " ")
            ui.label(f"Последний комментарий: {stamp}").classes("text-xs text-slate-400")
