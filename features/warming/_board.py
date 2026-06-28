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

from features.warming._board_checks import _ru_reason
from features.warming._board_dnd import (
    drop_into_idle,
    drop_into_warming,
    seed_card_refreshable,
)
from features.warming._board_header import _render_page_header
from features.warming._board_promote import render_promotion_block, render_trust_breakdown
from features.warming._board_spam import render_spam_badge
from features.warming._board_styling import (
    _PHASE_BAR_FILL,
    _PHASE_CHIP_SOLID,
    _STATE_BADGE,
    _STATE_LABEL,
    _TRUST_COLOR,
    _TRUST_LABEL_RU,
    _relative_eta,
)
from features.warming._termlog import render_card_log_panel
from schemas.warming import is_warming

# Public surface of this module (and the ``_relative_eta`` re-export that
# ``tests/features/test_warming_board_helpers.py`` imports from here).
__all__ = [
    "_BoardContext",
    "_card_signature",
    "_relative_eta",
    "_render_board",
    "_structural_signature",
]

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Callable, Coroutine

    from schemas.logs import LogEntry
    from schemas.warming import WarmingAccountState, WarmingBoardState


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
        card.promoted_to_nc,
    )


def _render_board(
    board: WarmingBoardState,
    ctx: _BoardContext,
) -> None:  # pragma: no cover
    """Build the board region: spec header + the two-column account layout.

    The per-card refresh dispatch table ``ctx.card_refresh`` is cleared and
    repopulated here — the caller (the page-level poll loop) re-runs this
    function only on structural changes (column move, card add/remove). LEFT
    column «Готовы к прогреву» = the idle drop target; RIGHT «В прогреве» =
    the warming drop target. Both keep the existing drag-drop wiring.
    """
    ctx.card_refresh.clear()
    _render_page_header(board, ctx)
    with ui.element("div").classes("tbw-grid w-full"):
        _render_ready_panel(ctx, board.idle)
        _render_warming_panel(ctx, board)


def _column_drop_handler(
    ctx: _BoardContext,
    key: str,
) -> Callable[[], Coroutine[object, object, None]]:  # pragma: no cover
    """Build the shared drop handler for a column (idle / warming)."""

    async def on_drop() -> None:
        account_id = ctx.drag["account_id"]
        source = ctx.drag.get("source")
        ctx.drag["account_id"] = None
        ctx.drag["source"] = None
        if not account_id:
            return
        if source == key:
            # Same-column drop — not a move; ignore so an accidental drag does
            # not restart (start_warming) or stop (stop_warming) the loop (П4).
            return
        if key == "warming":
            await drop_into_warming(ctx, account_id)
        else:
            await drop_into_idle(ctx, account_id)
        ctx.refresh()

    return on_drop


def _render_ready_panel(
    ctx: _BoardContext,
    cards: list[WarmingAccountState],
) -> None:  # pragma: no cover
    """LEFT column — «Готовы к прогреву» card; the idle-column drop target."""
    column = ui.element("div").classes("tb-card tbw-ready-col tb-dropzone")
    column.on("dragover.prevent", lambda: None)
    column.on("drop", _column_drop_handler(ctx, "idle"))
    with column:
        with ui.row().classes("w-full items-center justify-between"):
            ui.label("Готовы к прогреву").classes("tb-title")
            ui.label(str(len(cards))).classes("tbw-count-pill")
        if not cards:
            ui.label("Все доступные аккаунты в прогреве").classes(
                "text-[12px] text-[#9A9893] text-center py-6 w-full",
            )
        for card in cards:
            seed_card_refreshable(ctx, card, _render_card)


def _render_warming_panel(
    ctx: _BoardContext,
    board: WarmingBoardState,
) -> None:  # pragma: no cover
    """RIGHT column — «В прогреве» panel; the warming-column drop target."""
    cards = board.warming
    column = ui.element("div").classes("tb-card tbw-warming-col tb-dropzone")
    column.on("dragover.prevent", lambda: None)
    column.on("drop", _column_drop_handler(ctx, "warming"))
    with column:
        with ui.row().classes("w-full items-center justify-between"):
            with ui.row().classes("items-center gap-2"):
                with ui.element("div").classes("tbw-hero-tile"):
                    ui.icon("monitoring").classes("text-white text-base")
                ui.label("В прогреве").classes("tb-title-lg")
            running = board.active_count > 0
            ui.label("идёт прогрев" if running else "на паузе").classes(
                f"tb-badge tb-pulse {'tbw-pill-green' if running else 'tbw-pill-idle'}",
            )
        if not cards:
            ui.label("Перенесите аккаунты сюда, чтобы запустить прогрев").classes(
                "tbw-empty-zone w-full",
            )
        with ui.element("div").classes("tbw-cards-grid w-full"):
            for card in cards:
                seed_card_refreshable(ctx, card, _render_card)


def _render_card_header(card: WarmingAccountState) -> None:  # pragma: no cover
    """Name + state pill on left; bare trust score + label on right.

    The trust block reveals the full breakdown via a Quasar QMenu (click or
    hover; works on touch devices unlike a plain ``ui.tooltip``). A small info
    icon next to the band label makes the affordance discoverable.
    """
    with ui.row().classes("w-full items-start gap-2"):
        # left: name + state pill
        with ui.row().classes("flex-1 min-w-0 items-center gap-2 flex-wrap"):
            ui.label(card.label).classes("text-[13px] font-semibold text-slate-900 truncate")
            ui.label(_STATE_LABEL.get(card.state, card.state)).classes(
                f"text-[10px] px-2 py-0.5 rounded-full shrink-0 "
                f"{_STATE_BADGE.get(card.state, 'bg-slate-100 text-slate-600')}"
            )

        # right: bare trust number + coloured label + click/hover breakdown
        if card.trust_score is not None:
            band = card.trust_band or ""
            trust_block = ui.column().classes("items-end gap-0 shrink-0 cursor-pointer")
            with trust_block:
                ui.label(str(card.trust_score)).classes(
                    "text-[28px] font-bold text-slate-900 tabular-nums leading-none"
                )
                with ui.row().classes("items-center gap-1"):
                    ui.label(_TRUST_LABEL_RU.get(band, f"Trust {card.trust_score}")).classes(
                        f"text-[10px] font-medium leading-tight "
                        f"{_TRUST_COLOR.get(band, 'text-slate-500')}"
                    )
                    ui.icon("info").classes("text-xs text-slate-400")
            with (
                trust_block,
                ui.menu()
                .props("anchor='bottom right' self='top right'")
                .classes("bg-slate-900 text-white text-xs"),
            ):
                render_trust_breakdown(card)


def _render_spam_badge(ctx: _BoardContext, card: WarmingAccountState) -> None:  # pragma: no cover
    """Thin delegate to ``_board_spam.render_spam_badge``."""
    render_spam_badge(ctx, card)


def _render_card_footer(ctx: _BoardContext, card: WarmingAccountState) -> None:  # pragma: no cover
    """Cycle · daily actions · DM status · spam button (right-aligned)."""
    with ui.row().classes("w-full items-center"):
        ui.label(f"Цикл #{card.cycles_completed}").classes(
            "text-[10px] text-slate-500 tabular-nums"
        )
        _footer_sep()

        cap = card.daily_cap  # П2: auto per-account cap; fleet override retired
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
            ).tooltip("ЛС открыты движком — возраст и доверие достаточны.")
        else:
            ui.label("DM заблокирован").classes(
                "text-[10px] px-2 py-0.5 rounded-full bg-slate-100 text-slate-500"
            ).tooltip(
                "ЛС закрыты движком — не хватает возраста или доверия, "
                "либо не пройдена проверка готовности.",
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
        ).tooltip(
            "Этап взросления аккаунта: Новый → Адаптация → Развитие → Окрепший → Зрелый. "
            "«до X: N д» — сколько дней до следующего этапа.",
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
    """One account card — spec C.3 «В прогреве» card (blue) or a ready row.

    Warming-state cards use the pale-blue ``.tb-card-blue`` surface with the
    pipeline block; idle accounts render the slimmer ready-row variant (avatar
    + phone + trust + «Прогреть»). Both stay ``draggable`` so the kanban
    start/stop wiring is unchanged.
    """
    warming = card.state != "idle"
    surface = "tbw-card tb-card-blue" if warming else "tbw-card tbw-card-ready"
    element = ui.element("div").props("draggable").classes(f"{surface} w-full")
    # Record the source column so a drop back into the same column is a no-op
    # (audit П4): "warming" mirrors load_board's is_warming column split.
    source = "warming" if is_warming(card.state) else "idle"
    element.on(
        "dragstart",
        lambda aid=card.account_id, src=source: ctx.drag.update(account_id=aid, source=src),
    )

    with element, ui.column().classes("w-full gap-3"):
        _render_card_header(card)
        _render_phase_block(card)

        if warming:
            from features.warming._pipeline import render_cycle_pipeline  # noqa: PLC0415

            render_cycle_pipeline(card)

        # Readiness blocker — shown whenever readiness fails, not only idle:
        # a running account can degrade (proxy down, session dead, channels
        # removed), and the operator needs the blocking reasons either way.
        if card.readiness and not card.readiness.ready:
            reasons = ", ".join(_ru_reason(r) for r in card.readiness.reasons)
            ui.label(f"не готов: {reasons}").classes("text-[11px] tbw-text-red truncate")

        render_promotion_block(ctx.refresh, card)
        render_card_log_panel(ctx, card)
        _render_card_footer(ctx, card)
