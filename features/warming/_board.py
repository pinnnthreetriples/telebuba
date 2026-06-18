"""Kanban board rendering — summary chips, drag columns, account cards.

UI-thin per non-negotiable #1; every function is exercised manually and excluded
from coverage (``pragma: no cover``). The logic it calls is unit-tested in
``services.warming``. Pure styling tables live in ``_board_styling`` and
per-account check/spam formatters in ``_board_checks`` so this module stays
under the aislop file-length cap.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from nicegui import ui

from features.warming._board_checks import (
    _check_states,
    _ru_reason,
    _spam_badge_classes,
    _spam_badge_label,
    _spam_notify_type,
    _spam_outcome_label,
    _spam_tooltip,
)
from features.warming._board_styling import (
    _CHECK_DOT,
    _CHECK_TEXT,
    _ERROR_MAX_LEN,
    _ETA_DAY_SECONDS,
    _ETA_HOUR_SECONDS,
    _HEALTH_DOT,
    _PHASE_BAR_FILL,
    _PHASE_CHIP_CLASSES,
    _STATE_BADGE,
    _STATE_LABEL,
    _SUMMARY_CHIPS,
    _TRUST_BADGE,
    _TRUST_BAND_LABEL,
)
from schemas.warming import StartWarmingRequest, StopWarmingRequest
from services.spam_status import refresh_spam_status
from services.warming import WarmingNotReadyError, start_warming, stop_warming

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Callable

    from schemas.warming import WarmingAccountState, WarmingBoardState, WarmingSummary


@dataclass(frozen=True)
class _BoardContext:  # pragma: no cover
    """Board-wide render state shared by columns and cards (one per board build)."""

    drag: dict[str, str | None]
    refresh: Callable[[], asyncio.Task[None]]
    max_daily: int


def _board_signature(board: WarmingBoardState) -> tuple[object, ...]:  # pragma: no cover
    """A hashable digest of everything the board renders.

    The poll loop compares this between ticks and only rebuilds the DOM when it
    changes, so a quiet board never blinks.
    """
    cards = (*board.idle, *board.warming)
    return (
        board.channel_count,
        board.active_count,
        tuple(
            (
                card.account_id,
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
                None
                if card.readiness is None
                else (card.readiness.ready, tuple(card.readiness.reasons)),
            )
            for card in cards
        ),
    )


def _relative_eta(iso: str | None) -> str | None:  # pragma: no cover
    """Human ETA from now to an ISO timestamp, e.g. ``7 ч`` / ``12 мин``."""
    if not iso:
        return None
    try:
        target = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if target.tzinfo is None:
        target = target.replace(tzinfo=UTC)
    delta = (target - datetime.now(UTC)).total_seconds()
    if delta <= 0:
        return "сейчас"
    if delta < _ETA_HOUR_SECONDS:
        return f"{int(delta // 60)} мин"
    if delta < _ETA_DAY_SECONDS:
        return f"{int(delta // _ETA_HOUR_SECONDS)} ч"
    return f"{int(delta // _ETA_DAY_SECONDS)} д"


def _render_summary(summary: WarmingSummary) -> None:  # pragma: no cover
    with ui.row().classes("w-full gap-2 flex-wrap"):
        for label, field, cls in _SUMMARY_CHIPS:
            ui.label(f"{label}: {getattr(summary, field)}").classes(
                f"px-3 py-1.5 rounded-md text-xs font-medium {cls}",
            )


def _render_board(
    board: WarmingBoardState,
    drag: dict[str, str | None],
    refresh: Callable[[], asyncio.Task[None]],
) -> None:  # pragma: no cover
    _render_summary(board.summary)
    ctx = _BoardContext(drag=drag, refresh=refresh, max_daily=board.settings.max_daily_actions)
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
            try:
                await start_warming(StartWarmingRequest(account_id=account_id))
            except WarmingNotReadyError as exc:
                reasons = "; ".join(_ru_reason(reason) for reason in exc.reasons)
                ui.notify(f"Нельзя запустить: {reasons}", type="negative")
        else:
            await stop_warming(StopWarmingRequest(account_id=account_id))
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
            _render_card(ctx, card)


def _render_trust_badge(card: WarmingAccountState) -> None:  # pragma: no cover
    """Two-line key metric: score (large) over band label (small)."""
    band = card.trust_band or ""
    label_ru = _TRUST_BAND_LABEL.get(band, "")
    badge_classes = _TRUST_BADGE.get(band, "bg-slate-100 text-slate-600")
    tooltip = f"Trust {card.trust_score} · {label_ru}" if label_ru else f"Trust {card.trust_score}"
    with ui.column().classes(
        f"items-center gap-0 px-2.5 py-1 rounded shrink-0 {badge_classes}",
    ) as badge:
        ui.label(f"⛨ {card.trust_score}").classes("text-base font-bold leading-tight")
        if label_ru:
            ui.label(label_ru).classes("text-[10px] leading-tight")
    badge.tooltip(tooltip)


def _render_checks(card: WarmingAccountState) -> None:  # pragma: no cover
    """Strip of seven labelled chips under the trust badge — the full picture.

    Each chip is a small coloured dot + Russian label, with the tooltip
    explaining the specific signal (e.g. country pair for a geo mismatch).
    """
    with ui.row().classes("w-full gap-x-3 gap-y-1 flex-wrap"):
        for label, status, tooltip in _check_states(card):
            with ui.row().classes("items-center gap-1") as chip:
                ui.element("div").classes(f"w-2 h-2 rounded-full shrink-0 {_CHECK_DOT[status]}")
                ui.label(label).classes(f"text-[11px] {_CHECK_TEXT[status]}")
            chip.tooltip(tooltip)


def _render_spam_badge(ctx: _BoardContext, card: WarmingAccountState) -> None:  # pragma: no cover
    """Spam-status badge — always visible, always self-explaining.

    The badge text itself distinguishes a probe error (« ошибка проверки »)
    from a Telegram-side review (« на проверке Telegram ») from a plain
    no-probe-yet (« не проверен »); the tooltip carries the underlying
    ``spam_detail`` (e.g. "TimeoutError: timed out") so the operator knows
    where to look next.
    """
    status = card.spam_status or "unknown"
    text = _spam_badge_label(status, card.spam_detail)
    cls = _spam_badge_classes(status, card.spam_detail)
    tooltip = _spam_tooltip(status, card.spam_detail)
    with ui.row().classes("w-full items-center gap-2"):
        badge = ui.label(text).classes(f"w-fit text-[11px] px-2 py-0.5 rounded {cls}")
        if tooltip:
            badge.tooltip(tooltip)
        # Refresh is always available — a stale cached verdict (e.g. after a
        # proxy fix or a Telegram-side review ending) needs a manual re-probe,
        # and the operator should not have to delete the row from the DB to
        # invalidate it. ``refresh_spam_status`` passes ``force=True`` so it
        # bypasses ``spam_status_ttl_hours`` and actually re-probes @SpamBot.
        _render_spam_refresh_link(ctx, card.account_id)


def _render_spam_refresh_link(ctx: _BoardContext, account_id: str) -> None:  # pragma: no cover
    """Inline «проверить» link that triggers a real @SpamBot probe.

    Visible feedback in two places:
    1. ``button.props("loading")`` flips the Quasar button to a spinner while
       the probe runs — the operator sees the system is working. The button
       is rebuilt on ``ctx.refresh()`` so the loading state vanishes
       automatically once the new card renders.
    2. A toast announces the outcome — colour and text both reflect *which*
       outcome it is (clean / limited / Telegram-проверяет / ошибка / без
       вердикта), so the operator always knows whether the probe ran and
       what came back.
    """

    async def on_click() -> None:
        button.props("loading")
        try:
            verdict = await refresh_spam_status(account_id, force=True)
        except Exception as exc:  # noqa: BLE001 — UI handler surfaces any failure
            ui.notify(f"Не удалось проверить: {exc}", type="negative", timeout=6000)
            ctx.refresh()
            return
        label = _spam_outcome_label(verdict.status, verdict.detail)
        ui.notify(
            f"Спам-статус: {label}",
            type=_spam_notify_type(verdict.status, verdict.detail),
            timeout=6000,
        )
        ctx.refresh()

    button = (
        ui.button("проверить", on_click=on_click)
        .props("flat dense no-caps")
        .classes("text-[11px] text-blue-600 px-1 py-0")
    )


def _render_card_stats(card: WarmingAccountState, fleet_max_daily: int) -> None:  # pragma: no cover
    """Single-line stats footer. Per-card daily cap wins over fleet override.

    The fleet ``max_daily_actions`` setting is now an .env-driven override —
    when it's > 0 (legacy installs) it caps regardless of phase. Otherwise
    the per-account ``card.daily_cap`` (derived from phase + trust) applies.
    """
    parts: list[str] = []
    if card.age_hours is not None:
        days = int(card.age_hours // 24)
        parts.append(f"возраст {days} д" if days else f"возраст {int(card.age_hours)} ч")
    if card.warming_days is not None:
        parts.append(f"в прогреве {card.warming_days} д")
    parts.append("DM ✅" if card.dm_allowed else "DM 🔒")
    effective_cap = fleet_max_daily if fleet_max_daily > 0 else card.daily_cap
    daily = f"действий {card.daily_actions}"
    parts.append(f"{daily} / {effective_cap}" if effective_cap > 0 else daily)
    eta = _relative_eta(card.next_run_at)
    if eta:
        parts.append(f"⏭ {eta}")
    ui.label(" · ".join(parts)).classes("text-[11px] text-slate-500 truncate")


def _render_flood_wait_line(card: WarmingAccountState) -> None:  # pragma: no cover
    if card.state != "flood_wait":
        return
    remaining = _relative_eta(card.flood_wait_until)
    if remaining is None and card.flood_wait_seconds is None:
        return
    text = (
        f"🕒 flood-wait ещё {remaining}"
        if remaining
        else f"🕒 flood-wait {card.flood_wait_seconds} с"
    )
    ui.label(text).classes("text-[11px] text-amber-700 truncate")


def _render_quarantine_line(card: WarmingAccountState) -> None:  # pragma: no cover
    if card.quarantine_count <= 0:
        return
    ui.label(f"карантинов: {card.quarantine_count}").classes("text-[11px] text-orange-700 truncate")


def _render_error_line(card: WarmingAccountState) -> None:  # pragma: no cover
    """Show last error only when the card is in an error state and we have a message."""
    if card.state != "error" or not card.last_error:
        return
    parts = ["ошибка"]
    if card.last_action:
        parts.append(card.last_action)
    if card.last_channel:
        parts.append(f"в {card.last_channel}")
    head = " · ".join(parts)
    body = card.last_error
    if len(body) > _ERROR_MAX_LEN:
        body = body[: _ERROR_MAX_LEN - 1] + "…"
    ui.label(f"{head}: {body}").classes("text-[11px] text-red-600 truncate").tooltip(
        card.last_error,
    )


def _render_phase_block(card: WarmingAccountState) -> None:  # pragma: no cover
    """Phase chip + milestone hint + thin 4px progress bar.

    Visual structure follows the agent-B research: pill chip with phase
    name and emoji, right-aligned "до «X»: N дн" hint, and a 4px solid bar
    whose colour matches the chip. The bar disappears for the terminal
    ``warmed`` phase (no next boundary to point at).
    """
    if card.phase is None or card.phase_label is None:
        return
    chip_classes = _PHASE_CHIP_CLASSES.get(card.phase, _PHASE_CHIP_CLASSES["intro"])
    bar_fill = _PHASE_BAR_FILL.get(card.phase, _PHASE_BAR_FILL["intro"])
    with ui.row().classes("w-full items-center justify-between gap-2"):
        ui.label(card.phase_label).classes(
            f"w-fit text-[11px] px-2 py-0.5 rounded-full ring-1 font-medium {chip_classes}",
        )
        if card.phase != "warmed" and card.days_to_next_phase is not None:
            next_phase_label = _next_phase_label_short(card.phase)
            ui.label(f"до «{next_phase_label}»: {card.days_to_next_phase} д").classes(
                "text-[11px] text-slate-500 tabular-nums shrink-0",
            )
    if card.progress_to_next is not None:
        pct = round(card.progress_to_next * 100)
        with ui.row().classes("h-1 w-full rounded-full bg-slate-200 overflow-hidden"):
            ui.element("div").classes(f"h-full rounded-full {bar_fill}").style(
                f"width: {pct}%",
            )


_NEXT_PHASE_SHORT = {
    "intro": "Адаптация",
    "settling": "Развитие",
    "warming": "Окрепший",
    "active": "Зрелый",
}


def _next_phase_label_short(phase: str) -> str:  # pragma: no cover
    return _NEXT_PHASE_SHORT.get(phase, "")


def _render_card(ctx: _BoardContext, card: WarmingAccountState) -> None:  # pragma: no cover
    pulse = " tb-active" if card.state == "active" else ""
    element = (
        ui.card()
        .props("draggable")
        .classes(
            f"w-full p-4 gap-3 cursor-grab bg-white border border-slate-200 rounded-md{pulse}",
        )
    )
    element.on("dragstart", lambda aid=card.account_id: ctx.drag.update(account_id=aid))
    with element:
        # Header — dot, name, state pill, trust badge (key metric, two-line).
        with ui.row().classes("w-full items-center gap-2"):
            ui.element("div").classes(
                f"w-3 h-3 rounded-full shrink-0 {_HEALTH_DOT.get(card.health, 'bg-slate-400')}",
            )
            ui.label(card.label).classes("text-sm font-semibold truncate flex-1")
            ui.label(_STATE_LABEL.get(card.state, card.state)).classes(
                f"text-[11px] px-2 py-0.5 rounded shrink-0 {_STATE_BADGE.get(card.state, '')}",
            )
            if card.trust_score is not None:
                _render_trust_badge(card)
        # Per-signal checks — the "positive signals" view of the trust score.
        if card.trust_score is not None:
            _render_checks(card)
        # Lifecycle phase: chip + milestone hint + progress bar.
        _render_phase_block(card)
        # Spam status (always shown).
        _render_spam_badge(ctx, card)
        # Activity line + stats.
        meta = f"циклов {card.cycles_completed}"
        if card.last_event:
            meta = f"{meta} · {card.last_event}"
        ui.label(meta).classes("text-[11px] text-slate-500 truncate")
        _render_card_stats(card, ctx.max_daily)
        # Conditional diagnostic lines — only render when relevant.
        _render_flood_wait_line(card)
        _render_quarantine_line(card)
        _render_error_line(card)
        if card.readiness and not card.readiness.ready:
            reasons = ", ".join(_ru_reason(reason) for reason in card.readiness.reasons)
            ui.label(f"не готов: {reasons}").classes("text-[11px] text-red-600 truncate")
