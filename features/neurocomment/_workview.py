"""Neurocomment work-view rendering — «Доска работ» board + «Капчи» strip + drill-down.

Split out of ``_page`` to stay within the file-size budget. UI-thin, fully
``pragma: no cover``; the pure display helpers it uses live in ``_page`` (and are
unit-tested there). ``_page`` imports ``render_work_view`` lazily, so this module
can import the helpers from ``_page`` at module level without a cycle.

Re-skinned to the design palette (spec C.4): a collapsible white «Доска работ» card
holding a channels table with boardMap status pills, the per-account cards, and a
minimalist «Капчи» control strip (solver mode + outcome counters + retry/skip drill-down).
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
    channel_status_colors,
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
        ui.label("Кампания не найдена").classes("tb-muted")
        return
    _render_board_card(board)
    _render_solver_controls(board)


def _card_header(  # pragma: no cover
    title: str,
    count_text: str,
    count_bg: str,
    count_color: str,
) -> None:
    """A card header row: title + a count pill, with a hairline bottom border."""
    with (
        ui.row()
        .classes("w-full items-center justify-between flex-nowrap")
        .style(
            "padding-bottom:12px;border-bottom:1px solid #F0EEEB;margin-bottom:12px",
        ),
        ui.row().classes("items-center gap-2 flex-nowrap"),
    ):
        ui.label(title).classes("tb-title")
        ui.html(
            f'<span style="font-size:11px;font-weight:600;background:{count_bg};'
            f'color:{count_color};border-radius:9999px;padding:2px 9px">{count_text}</span>',
        )


def _render_board_card(board: NeurocommentBoard) -> None:  # pragma: no cover
    """«Доска работ» — channels table (boardMap pills) + per-account cards."""
    with ui.element("div").classes("tb-card w-full").style("padding:16px 18px"):
        _card_header(
            "Доска работ",
            f"{len(board.accounts)} аккаунтов",
            "#EEF4FF",
            "#0066FF",
        )
        _render_channels_table(board)
        _render_accounts(board)


def _render_channels_table(board: NeurocommentBoard) -> None:  # pragma: no cover
    if not board.channels:
        ui.label("Каналов пока нет").classes("tb-muted")
        return
    with ui.element("div").classes("tb-scroll w-full").style("overflow-x:auto"):
        table = ui.element("table").classes("tb-table").style("min-width:520px")
        with table:
            with ui.element("thead"), ui.element("tr"):
                for col in ("Канал", "Готовность", "Статус"):
                    ui.html(f"<th>{col}</th>")
            with ui.element("tbody"):
                for row in board.channels:
                    _render_channel_row(row)


def _render_channel_row(row) -> None:  # noqa: ANN001  # pragma: no cover
    bg, color = channel_status_colors(row.status)
    with ui.element("tr"):
        ui.html(
            f'<td style="color:#0066FF;font-weight:500">{row.channel}</td>'
            f'<td style="color:#74726E">{row.ready_accounts}/{row.total_accounts}</td>'
            f'<td><span class="tb-badge" style="background:{bg};color:{color};'
            f'font-size:11.5px;font-weight:600"><span class="tb-badge-dot"></span>'
            f"{channel_status_label(row.status)}</span></td>",
        )
    # Failed-challenge drill-down spans the full width on its own row.
    if row.status == "bot_challenge":
        with ui.element("tr"), ui.element("td").props('colspan="3"'):
            _render_challenge_drilldown(row.channel)


def _render_challenge_drilldown(channel: str) -> None:  # pragma: no cover
    """Lazy expansion: on open, list the channel's recent failed challenges + actions.

    Loads on demand (not on every board poll) so the 4 s refresh stays cheap.
    """
    expansion = (
        ui.expansion("Капчи бота")
        .props("dense")
        .classes("w-full")
        .style(
            "font-size:12px",
        )
    )
    with expansion:
        rows_box = ui.column().classes("w-full gap-1")

    async def load() -> None:
        result = await list_channel_challenges(channel, _CHALLENGE_DRILLDOWN_LIMIT)
        rows_box.clear()
        with rows_box:
            if not result.rows:
                ui.label("Записей пока нет").classes("tb-muted")
            for r in result.rows:
                with ui.row().classes("w-full items-center justify-between gap-2 flex-nowrap"):
                    ui.label(challenge_summary(r)).style("font-size:12px;color:#5C5C5C")
                    with ui.row().classes("gap-1 flex-nowrap"):
                        retry = ui.button(
                            icon="refresh",
                            on_click=lambda _e=None, a=r.account_id, c=r.channel: _on_retry(a, c),
                        )
                        retry.props("flat dense round").classes("tb-icon-btn").style(
                            "width:28px;height:28px",
                        )
                        retry.tooltip("Переонбордить")
                        # Dark «Пройти» pill — the spec's captcha action button.
                        passb = ui.button(
                            "Пройти",
                            on_click=lambda _e=None, a=r.account_id, c=r.channel: _on_skip(a, c),
                        )
                        passb.props("flat no-caps dense").classes("tb-btn tb-btn-dark").style(
                            "padding:5px 13px;font-size:11.5px",
                        )

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


def _render_accounts(board: NeurocommentBoard) -> None:  # pragma: no cover
    if not board.accounts:
        return
    with ui.column().classes("w-full gap-2").style("margin-top:14px"):
        for card in board.accounts:
            _render_account_card(card)


def _render_account_card(card) -> None:  # noqa: ANN001  # pragma: no cover
    # Thin left accent by health (green ready / red blocked) — at-a-glance scanning,
    # mirroring the warming card stripe.
    ready = card.health == "ready"
    accent = "#12A150" if ready else "#E5372A"
    badge_bg, badge_color = ("#DDF7E9", "#12A150") if ready else ("#FDE6E2", "#E5372A")
    with ui.element("div").style(
        f"width:100%;background:#fff;border:1px solid #E6E5E3;border-left:4px solid {accent};"
        "border-radius:12px;padding:12px 14px;display:flex;flex-direction:column;gap:4px",
    ):
        with ui.row().classes("w-full items-center justify-between flex-nowrap"):
            ui.label(card.label).classes("tb-title tb-mono")
            ui.html(
                f'<span class="tb-badge" style="background:{badge_bg};color:{badge_color};'
                f'font-size:11px;font-weight:600"><span class="tb-badge-dot"></span>'
                f"{health_label(card.health)}</span>",
            )
        ui.label(
            f"Комментариев за час: {card.comments_last_hour}/{card.max_comments_per_hour} · "
            f"за сутки: {card.comments_today}",
        ).style("font-size:12px;color:#74726E")
        ui.label(
            f"Доверие: {card.trust_score} ({card.trust_band})"
            + (f" · спам: {card.spam_status}" if card.spam_status else ""),
        ).style("font-size:12px;color:#9A9893")
        if card.last_comment_at:
            stamp = card.last_comment_at[:16].replace("T", " ")
            ui.label(f"Последний комментарий: {stamp}").style("font-size:11.5px;color:#9A9893")


def _render_solver_controls(board: NeurocommentBoard) -> None:  # pragma: no cover
    """Minimalist «Капчи» strip: solver switch + window toggle + inline outcome counters."""
    channels = [row.channel for row in board.channels]
    global_enabled = settings.neurocomment.challenge_solver_enabled
    default_text = "ВКЛ" if global_enabled else "ВЫКЛ"
    solver_options = {
        "follow": f"Капчи: по настройке ({default_text})",
        "on": "Капчи: ВКЛ",
        "off": "Капчи: ВЫКЛ",
    }
    with ui.element("div").classes("tb-card-soft w-full").style("padding:13px 14px"):  # noqa: SIM117
        with ui.row().classes("w-full items-center justify-between gap-3 flex-wrap"):
            with ui.row().classes("items-center gap-2 flex-wrap"):
                ui.html(
                    '<div style="width:28px;height:28px;border-radius:8px;background:#EEF4FF;'
                    'color:#0066FF;display:flex;align-items:center;justify-content:center">'
                    '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" '
                    'stroke="currentColor" stroke-width="1.9" stroke-linecap="round" '
                    'stroke-linejoin="round">'
                    '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>'
                    '<path d="m9 12 2 2 4-4"/></svg></div>',
                )
                ui.label("Решение капч").classes("tb-title")
                help_icon = (
                    ui.icon("help_outline")
                    .style("color:#9A9893;font-size:15px")
                    .classes(
                        "cursor-help",
                    )
                )
                help_icon.tooltip(
                    "Настройка автоматического решения приветственных капч ботов "
                    "в группах обсуждения каналов при онбординге или отправке комментариев."
                )
                switch = (
                    ui.select(solver_options, value=solver_switch_key(board.solver_enabled))
                    .props("dense outlined options-dense")
                    .classes("max-w-[240px]")
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
                    .classes("max-w-[130px]")
                )
                counts = ui.label("").style(
                    "font-size:12px;color:#74726E;font-variant-numeric:tabular-nums",
                )
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
