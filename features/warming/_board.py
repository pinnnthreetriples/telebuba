"""Kanban board rendering — summary chips, drag columns, account cards.

UI-thin per non-negotiable #1; every function is exercised manually and excluded
from coverage (``pragma: no cover``). The logic it calls is unit-tested in
``services.warming``. Pure styling tables live in ``_board_styling`` and
per-account check/spam formatters in ``_board_checks`` so this module stays
under the aislop file-length cap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from nicegui import ui

from features.warming._board_checks import (
    _check_states,
    _ru_reason,
)
from features.warming._board_dnd import (
    drop_into_idle,
    drop_into_warming,
    seed_card_refreshable,
)
from features.warming._board_spam import render_spam_badge
from features.warming._board_styling import (
    _CHECK_CHIP,
    _CHECK_CHIP_DOT,
    _PHASE_BAR_FILL,
    _PHASE_CHIP_SOLID,
    _STATE_BADGE,
    _STATE_LABEL,
    _STATUS_ACTION_LABEL,
    _STATUS_DOT,
    _STRIPE_CLS,
    _SUMMARY_CHIPS,
    _TRUST_COLOR,
    _TRUST_LABEL_RU,
    _relative_eta,
)
from features.warming._termlog import render_card_log_panel

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Callable, Coroutine

    from schemas.logs import LogEntry
    from schemas.warming import WarmingAccountState, WarmingBoardState, WarmingSummary


@dataclass
class _BoardContext:  # pragma: no cover
    """Board-wide render state shared by columns and cards (one per board build).

    ``card_store`` and ``card_refresh`` together implement the per-card refresh
    dispatch recommended by NiceGUI maintainers (discussion #2772): each card
    owns its own ``ui.refreshable`` instance, the poll callback updates the
    store entry and refreshes only the cards whose signature changed.
    """

    drag: dict[str, str | None]
    refresh: Callable[[], asyncio.Task[None]]
    max_daily: int
    card_store: dict[str, WarmingAccountState] = field(default_factory=dict)
    card_refresh: dict[str, Any] = field(default_factory=dict)
    # Per-card activity-log panel state — kept here (not in the per-card
    # refreshable) so an open panel and its fetched rows survive the 4s poll.
    card_expanded: dict[str, bool] = field(default_factory=dict)
    card_logs: dict[str, list[LogEntry]] = field(default_factory=dict)
    card_log_sig: dict[str, tuple[int, ...]] = field(default_factory=dict)
    on_toggle_log: Callable[[str], Coroutine[object, object, None]] | None = None


def _structural_signature(board: WarmingBoardState) -> tuple[object, ...]:  # pragma: no cover
    """Digest of fields that drive *structural* re-renders (column moves, counts).

    Changes here force a full board rebuild because the per-card refreshables
    have to be re-wired into the new column layout. Stable for an idle board.
    """
    return (
        board.channel_count,
        board.active_count,
        tuple((card.account_id, "idle") for card in board.idle),
        tuple((card.account_id, "warming") for card in board.warming),
        # Summary roll-ups can drift without a column move (e.g. sleeping→error
        # flips `attention`, a trust-band shift flips the trust counts), so they
        # must be in the digest or the header chips go stale against the cards.
        (
            board.summary.ready,
            board.summary.attention,
            board.summary.trust_healthy,
            board.summary.trust_watch,
            board.summary.trust_risk,
        ),
    )


def _card_signature(card: WarmingAccountState) -> tuple[object, ...]:  # pragma: no cover
    """Digest of fields that drive a *single* card's rendered content.

    ``progress_to_next`` is now quantised to 1 % at the source
    (``services/warming/pacing.py:_phase_progress``) so the µs drift from
    recomputing ``age_hours`` every 4-second poll no longer flips this
    signature. ``card.state`` indirectly carries `dm_allowed` /
    `flood_wait_*` transitions, which is what the chip strip reads.
    """
    return (
        card.state,
        card.health,
        card.cycles_completed,
        card.last_event,
        card.next_run_at,
        card.last_error,
        card.last_action,
        card.last_channel,
        card.trust_score,
        card.trust_band,
        tuple(card.trust_reasons),
        card.spam_status,
        card.spam_detail,
        card.daily_actions,
        card.dm_allowed,
        card.quarantine_count,
        card.flood_wait_until,
        card.flood_wait_seconds,
        card.phone_country,
        card.proxy_country,
        card.phase,
        card.daily_cap,
        card.progress_to_next,
        card.days_to_next_phase,
        card.warming_days,
        None if card.readiness is None else (card.readiness.ready, tuple(card.readiness.reasons)),
    )


def _render_summary(summary: WarmingSummary) -> None:  # pragma: no cover
    with ui.row().classes("w-full gap-2 flex-wrap"):
        for label, field, cls in _SUMMARY_CHIPS:
            ui.label(f"{label}: {getattr(summary, field)}").classes(
                f"px-3 py-1.5 rounded-md text-xs font-medium {cls}",
            )


def _render_board(
    board: WarmingBoardState,
    ctx: _BoardContext,
) -> None:  # pragma: no cover
    """Build the whole board: summary chips and two drag columns.

    The per-card refresh dispatch table ``ctx.card_refresh`` is cleared and
    repopulated here — the caller (the page-level poll loop) re-runs this
    function only on structural changes (column move, card add/remove).
    """
    ctx.card_refresh.clear()
    _render_summary(board.summary)
    with ui.row().classes("w-full gap-4 items-stretch flex-wrap"):
        _render_column(ctx, "Простой", "idle", board.idle, "border-slate-300")
        _render_column(
            ctx,
            f"Прогрев · активно: {board.active_count}",
            "warming",
            board.warming,
            "border-green-400",
        )


def _render_column(
    ctx: _BoardContext,
    title: str,
    key: str,
    cards: list[WarmingAccountState],
    border: str,
) -> None:  # pragma: no cover
    column = ui.column().classes(
        f"tb-dropzone flex-1 min-w-[320px] p-3 gap-3 rounded-lg border-2 border-dashed "
        f"{border} bg-white min-h-[240px]",
    )

    async def on_drop() -> None:
        account_id = ctx.drag["account_id"]
        ctx.drag["account_id"] = None
        if not account_id:
            return
        if key == "warming":
            await drop_into_warming(ctx, account_id)
        else:
            await drop_into_idle(ctx, account_id)
        ctx.refresh()

    column.on("dragover.prevent", lambda: None)
    column.on("drop", on_drop)
    with column:
        with ui.row().classes("w-full items-center justify-between"):
            ui.label(title).classes("text-sm font-semibold text-slate-700")
            ui.label(str(len(cards))).classes(
                "text-xs px-2 py-0.5 rounded bg-slate-100 text-slate-600",
            )
        if not cards:
            ui.label("Перетащите аккаунты сюда").classes("text-xs text-slate-400 italic")
        for card in cards:
            seed_card_refreshable(ctx, card, _render_card)


def _render_card_header(card: WarmingAccountState) -> None:  # pragma: no cover
    """Name + state pill on left; bare trust score + label on right."""
    with ui.row().classes("w-full items-start gap-2"):
        # left: name + state pill
        with ui.row().classes("flex-1 min-w-0 items-center gap-2 flex-wrap"):
            ui.label(card.label).classes("text-[13px] font-semibold text-slate-900 truncate")
            ui.label(_STATE_LABEL.get(card.state, card.state)).classes(
                f"text-[10px] px-2 py-0.5 rounded-full shrink-0 "
                f"{_STATE_BADGE.get(card.state, 'bg-slate-100 text-slate-600')}"
            )

        # right: bare trust number + coloured label (no coloured badge background)
        if card.trust_score is not None:
            band = card.trust_band or ""
            with ui.column().classes("items-end gap-0 shrink-0"):
                ui.label(str(card.trust_score)).classes(
                    "text-[28px] font-bold text-slate-900 tabular-nums leading-none"
                )
                ui.label(_TRUST_LABEL_RU.get(band, f"Trust {card.trust_score}")).classes(
                    f"text-[10px] font-medium leading-tight "
                    f"{_TRUST_COLOR.get(band, 'text-slate-500')}"
                )


def _render_checks(card: WarmingAccountState) -> None:  # pragma: no cover
    """Rectangular health-check chips: coloured dot + Russian label + tooltip."""
    with ui.row().classes("w-full gap-1 flex-wrap"):
        for label, status, tooltip in _check_states(card):
            chip_cls = _CHECK_CHIP.get(status, _CHECK_CHIP["ok"])
            dot_cls = _CHECK_CHIP_DOT.get(status, "bg-slate-400")
            with ui.row().classes(
                f"items-center gap-1.5 px-2 py-1 rounded border text-[10px] {chip_cls}"
            ) as chip:
                ui.element("div").classes(f"w-1.5 h-1.5 rounded-full shrink-0 {dot_cls}")
                ui.label(label.capitalize())
            chip.tooltip(tooltip)


def _render_spam_badge(ctx: _BoardContext, card: WarmingAccountState) -> None:  # pragma: no cover
    """Thin delegate to ``_board_spam.render_spam_badge``."""
    render_spam_badge(ctx, card)


def _render_status_line(card: WarmingAccountState) -> None:  # pragma: no cover
    """Coloured dot + action text + secondary between pipeline and footer.

    Per-state text is a dict lookup, not an if/elif dispatch on card.state.
    "sleeping" leaves the secondary empty — that detail lives in the info box
    below (avoid the duplicate).
    """
    eta = _relative_eta(card.flood_wait_until)
    flood_secondary = f"ещё {eta}" if eta and eta != "сейчас" else "истекает"
    text = {
        "active": (
            _STATUS_ACTION_LABEL.get(card.last_action or "", "выполняет цикл"),
            "выполняется сейчас",
        ),
        "error": (f"ошибка: {card.last_action or 'цикл'}", "ожидает повторной попытки"),
        "flood_wait": ("flood-wait активен", flood_secondary),
        "sleeping": ("спит по расписанию", ""),
        "quarantine": ("карантин", "цикл приостановлен"),
    }.get(card.state)
    if text is None:
        return
    primary, secondary = text
    dot_cls = _STATUS_DOT.get(card.state, "bg-slate-400")

    with ui.row().classes("w-full items-center gap-2"):
        ui.element("div").classes(f"w-2 h-2 rounded-full shrink-0 tb-live-dot {dot_cls}")
        with ui.row().classes("items-baseline gap-0 flex-1 min-w-0"):
            ui.label(primary).classes("text-[11px] text-slate-700 truncate tb-live")
            ui.label("...").classes("text-[11px] text-slate-700 shrink-0 tb-live-dots")
        if secondary:
            ui.label(secondary).classes("text-[10px] text-slate-400 shrink-0")


def _render_card_footer(ctx: _BoardContext, card: WarmingAccountState) -> None:  # pragma: no cover
    """Cycle · daily actions · DM status · spam button (right-aligned)."""
    with ui.row().classes("w-full items-center"):
        ui.label(f"Цикл #{card.cycles_completed}").classes(
            "text-[10px] text-slate-500 tabular-nums"
        )
        _footer_sep()

        cap = ctx.max_daily if ctx.max_daily > 0 else card.daily_cap
        if cap > 0:
            ui.label(f"Действия {card.daily_actions}/{cap}").classes(
                "text-[10px] text-slate-500 tabular-nums"
            )
        else:
            ui.label(f"Действия {card.daily_actions}").classes(
                "text-[10px] text-slate-500 tabular-nums"
            )
        _footer_sep()

        if card.dm_allowed:
            ui.label("DM разрешён").classes(
                "text-[10px] px-2 py-0.5 rounded-full bg-green-100 text-green-700 font-medium"
            )
        else:
            ui.label("DM заблокирован").classes(
                "text-[10px] px-2 py-0.5 rounded-full bg-slate-100 text-slate-500"
            )

        ui.element("div").classes("flex-1")  # push spam button to right
        _render_spam_badge(ctx, card)


def _footer_sep() -> None:  # pragma: no cover
    """1px vertical separator between footer items."""
    ui.element("div").classes("w-px h-3 bg-slate-200 mx-2 shrink-0")


def _strip_emoji(text: str) -> str:  # pragma: no cover
    """Remove leading emoji and surrounding whitespace from a label string."""
    return re.sub(r"^[\U00010000-\U0010ffff☀-⟿\s]+", "", text).strip()


def _render_phase_block(card: WarmingAccountState) -> None:  # pragma: no cover
    """Phase chip + milestone hint + thin 4px progress bar.

    Visual structure follows the agent-B research: pill chip with phase
    name and emoji, right-aligned "до «X»: N дн" hint, and a 4px solid bar
    whose colour matches the chip. The bar disappears for the terminal
    ``warmed`` phase (no next boundary to point at).
    """
    if card.phase is None or card.phase_label is None:
        return
    chip_classes = _PHASE_CHIP_SOLID.get(card.phase, _PHASE_CHIP_SOLID["intro"])
    bar_fill = _PHASE_BAR_FILL.get(card.phase, _PHASE_BAR_FILL["intro"])
    with ui.row().classes("w-full items-center justify-between gap-2"):
        ui.label(_strip_emoji(card.phase_label)).classes(
            f"w-fit text-[10px] px-2 py-0.5 rounded font-medium {chip_classes}",
        )
        if card.phase != "warmed" and card.days_to_next_phase is not None:
            next_phase_label = _next_phase_label_short(card.phase)
            ui.label(f"до {next_phase_label}: {card.days_to_next_phase} д").classes(
                "text-[11px] text-slate-500 tabular-nums shrink-0",
            )
    if card.progress_to_next is not None:
        pct = round(card.progress_to_next * 100)
        # Guard on the raw value, not the rounded pct: a tiny progress (0.004)
        # rounds to 0 but should still show a sliver, not a blank bar.
        bar_style = f"width: {pct}%" + ("; min-width: 6px" if card.progress_to_next > 0 else "")
        with ui.row().classes("h-1.5 w-full rounded-full bg-slate-200 overflow-hidden"):
            ui.element("div").classes(f"h-full rounded-full {bar_fill}").style(bar_style)


_NEXT_PHASE_SHORT = {
    "intro": "Адаптации",
    "settling": "Развития",
    "warming": "Окрепшего",
    "active": "Зрелого",
}


def _next_phase_label_short(phase: str) -> str:  # pragma: no cover
    return _NEXT_PHASE_SHORT.get(phase, "")


def _render_card(ctx: _BoardContext, card: WarmingAccountState) -> None:  # pragma: no cover
    stripe_cls = _STRIPE_CLS.get(card.state, "bg-slate-200")
    pulse = " tb-active" if card.state == "active" else ""

    # Use a raw div — NOT ui.card() — for full stripe + border-radius control.
    element = (
        ui.element("div")
        .props("draggable")
        .classes(
            f"w-full flex rounded-xl overflow-hidden bg-white cursor-grab shadow-sm"
            f" hover:shadow-md transition-shadow{pulse}"
        )
    )
    element.on("dragstart", lambda aid=card.account_id: ctx.drag.update(account_id=aid))

    with element:
        # Coloured left stripe (6px)
        ui.element("div").classes(f"w-1.5 shrink-0 {stripe_cls}")

        # Card body
        with ui.element("div").classes("flex-1 min-w-0 p-4 flex flex-col gap-3"):
            _render_card_header(card)

            if card.trust_score is not None:
                _render_checks(card)

            _render_phase_block(card)

            # Pipeline + status line for non-idle accounts
            if card.state != "idle":
                from features.warming._pipeline import (  # noqa: PLC0415
                    render_cycle_pipeline,
                )

                render_cycle_pipeline(card, status_line=lambda: _render_status_line(card))

            # Readiness blocker — shown whenever readiness fails, not only idle:
            # a running account can degrade (proxy down, session dead, channels
            # removed), and the operator needs the blocking reasons either way.
            if card.readiness and not card.readiness.ready:
                reasons = ", ".join(_ru_reason(r) for r in card.readiness.reasons)
                ui.label(f"не готов: {reasons}").classes("text-[11px] text-red-600 truncate")

            render_card_log_panel(ctx, card)
            _render_card_footer(ctx, card)
