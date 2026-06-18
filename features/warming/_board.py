"""Kanban board rendering — summary chips, drag columns, account cards.

UI-thin per non-negotiable #1; every function is exercised manually and excluded
from coverage (``pragma: no cover``). The logic it calls is unit-tested in
``services.warming``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from nicegui import ui

from schemas.warming import StartWarmingRequest, StopWarmingRequest
from services.spam_status import refresh_spam_status
from services.warming import WarmingNotReadyError, start_warming, stop_warming

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Callable

    from schemas.warming import WarmingAccountState, WarmingBoardState, WarmingSummary

_BOARD_POLL_SECONDS = 4.0
_ETA_HOUR_SECONDS = 3600
_ETA_DAY_SECONDS = 86_400

_HEALTH_DOT = {
    "ok": "bg-green-500",
    "warn": "bg-amber-500",
    "fail": "bg-red-500",
    "idle": "bg-slate-400",
}
_STATE_LABEL = {
    "idle": "Простой",
    "active": "Прогрев",
    "sleeping": "Сон",
    "flood_wait": "Flood-ожидание",
    "quarantine": "Карантин",
    "error": "Ошибка",
}
_STATE_BADGE = {
    "idle": "text-slate-600 bg-slate-100",
    "active": "text-green-700 bg-green-100",
    "sleeping": "text-amber-700 bg-amber-100",
    "flood_wait": "text-amber-800 bg-amber-100",
    "quarantine": "text-orange-700 bg-orange-100",
    "error": "text-red-700 bg-red-100",
}
_SPAM_BADGE = {
    "clean": ("✅ чисто", "text-green-700 bg-green-100"),
    "limited": ("⛔ ограничен", "text-red-700 bg-red-100"),
    "unknown": ("❓ не проверён", "text-slate-600 bg-slate-100"),
}

# Readiness reasons are produced (in English) by ``services.warming`` and are
# also written to logs/tests; translate them here at the UI edge only.
_READINESS_REASON_RU = {
    "no proxy": "нет прокси",
    "proxy failed": "прокси не работает",
    "no channels": "нет каналов",
}

_SUMMARY_CHIPS = (
    ("Всего", "total", "bg-slate-100 text-slate-700"),
    ("Прогрев", "warming", "bg-green-100 text-green-700"),
    ("Готовы", "ready", "bg-emerald-100 text-emerald-700"),
    ("Внимание", "attention", "bg-orange-100 text-orange-700"),
    ("⛨ здоровы", "trust_healthy", "bg-green-100 text-green-700"),
    ("⛨ watch", "trust_watch", "bg-amber-100 text-amber-700"),
    ("⛨ риск", "trust_risk", "bg-red-100 text-red-700"),
)

_TRUST_BADGE = {
    "excellent": "bg-green-100 text-green-700",
    "good": "bg-emerald-100 text-emerald-700",
    "watch": "bg-amber-100 text-amber-700",
    "at_risk": "bg-orange-100 text-orange-700",
    "critical": "bg-red-100 text-red-700",
}
_TRUST_BAND_LABEL = {
    "excellent": "отлично",
    "good": "норма",
    "watch": "под наблюдением",
    "at_risk": "риск",
    "critical": "критично",
}
# Translate trust reasons at the UI edge — services/trust.py keeps them in
# English (testable, log-friendly), the warming card surfaces them in Russian.
# Same pattern as ``_READINESS_REASON_RU`` above. Dynamic prefixes are matched
# in ``_ru_trust_reason``.
_TRUST_REASON_RU = {
    "spam-limited": "спам-ограничения",
    "recent flood": "недавний flood-wait",
    "geo mismatch": "страна номера ≠ страна прокси",
    "geo unknown": "страна не определена",
    "proxy failed": "прокси не работает",
    "new account": "новый аккаунт",
}
_TRUST_REASONS_INLINE_LIMIT = 2
_ERROR_MAX_LEN = 80


def _ru_reason(reason: str) -> str:  # pragma: no cover
    if reason in _READINESS_REASON_RU:
        return _READINESS_REASON_RU[reason]
    if reason.startswith("session "):
        return f"сессия: {reason[len('session ') :]}"
    return reason


def _ru_trust_reason(reason: str) -> str:
    """Translate a single trust reason for the UI; falls back to the raw string."""
    if reason in _TRUST_REASON_RU:
        return _TRUST_REASON_RU[reason]
    if reason.startswith("status "):
        return f"сессия: {reason[len('status ') :]}"
    if reason.startswith("quarantined x"):
        return f"карантин ×{reason[len('quarantined x') :]}"
    return reason


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
                card.daily_actions,
                card.dm_allowed,
                card.quarantine_count,
                card.flood_wait_until,
                card.flood_wait_seconds,
                card.phone_country,
                card.proxy_country,
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
        f"tb-dropzone flex-1 min-w-[320px] p-3 gap-2 rounded-lg border-2 border-dashed "
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
    """Badge ``⛨ 80 · норма`` with full reason list as a tooltip.

    Inline reasons under the header (``_render_trust_reasons``) keep the most
    important context visible without a hover; the tooltip carries the full
    set for completeness.
    """
    band = card.trust_band or ""
    label_ru = _TRUST_BAND_LABEL.get(band, band or "n/a")
    badge_classes = _TRUST_BADGE.get(band, "bg-slate-100 text-slate-600")
    tooltip = f"Trust {card.trust_score} · {label_ru}"
    if card.trust_reasons:
        tooltip = f"{tooltip}: " + ", ".join(_ru_trust_reason(r) for r in card.trust_reasons)
    ui.label(f"⛨ {card.trust_score} · {label_ru}").classes(
        f"text-[11px] px-1.5 py-0.5 rounded {badge_classes}",
    ).tooltip(tooltip)


def _render_trust_reasons(card: WarmingAccountState) -> None:  # pragma: no cover
    """Inline top reasons under the header — only when the band warrants attention."""
    if card.trust_band in (None, "", "excellent"):
        return
    if not card.trust_reasons:
        return
    shown = [_ru_trust_reason(r) for r in card.trust_reasons[:_TRUST_REASONS_INLINE_LIMIT]]
    extra = len(card.trust_reasons) - len(shown)
    text = "⛨ " + " · ".join(shown)
    if extra > 0:
        text += f" · +ещё {extra}"
    ui.label(text).classes("text-[11px] text-slate-500 italic truncate")


def _render_spam_badge(ctx: _BoardContext, card: WarmingAccountState) -> None:  # pragma: no cover
    """Spam-status badge — always shown, with a refresh link when unknown.

    A missing row is rendered the same as ``unknown`` ("не проверён"). That way
    the card communicates "no @SpamBot check has been run yet" explicitly,
    instead of silently hiding the field — which is what made the original
    setup look inconsistent with the accounts page.
    """
    status = card.spam_status or "unknown"
    text, cls = _SPAM_BADGE.get(status, (status, "text-slate-600 bg-slate-100"))
    with ui.row().classes("w-full items-center gap-2"):
        badge = ui.label(text).classes(f"w-fit text-[11px] px-1.5 py-0.5 rounded {cls}")
        if card.spam_detail:
            badge.tooltip(card.spam_detail)
        if status == "unknown":
            _render_spam_refresh_link(ctx, card.account_id)


def _render_spam_refresh_link(ctx: _BoardContext, account_id: str) -> None:  # pragma: no cover
    async def on_click() -> None:
        try:
            verdict = await refresh_spam_status(account_id, force=True)
        except Exception as exc:  # noqa: BLE001 — UI handler surfaces any failure
            ui.notify(f"Не удалось проверить: {exc}", type="negative")
            return
        ui.notify(f"Spam-статус: {verdict.status}", type="positive")
        ctx.refresh()

    ui.button("проверить", on_click=on_click).props("flat dense no-caps").classes(
        "text-[11px] text-blue-600 px-1 py-0",
    )


def _render_card_stats(card: WarmingAccountState, max_daily: int) -> None:  # pragma: no cover
    parts: list[str] = []
    if card.age_hours is not None:
        days = int(card.age_hours // 24)
        parts.append(f"возраст {days} д" if days else f"возраст {int(card.age_hours)} ч")
    parts.append("DM ✅" if card.dm_allowed else "DM 🔒")
    daily = f"действий {card.daily_actions}"
    parts.append(f"{daily}/{max_daily}" if max_daily > 0 else daily)
    eta = _relative_eta(card.next_run_at)
    if eta:
        parts.append(f"⏭ {eta}")
    ui.label(" · ".join(parts)).classes("text-[11px] text-slate-500 truncate")


def _render_geo_chip(card: WarmingAccountState) -> None:  # pragma: no cover
    """Show a phone/proxy country chip only when it tells the operator something.

    Hidden when geo is matched, fully unknown, or partial (would be noise).
    A mismatch is the case worth surfacing — it pairs with the
    ``страна номера ≠ страна прокси`` trust reason.
    """
    phone = card.phone_country
    proxy = card.proxy_country
    if not phone or not proxy or phone == proxy:
        return
    ui.label(f"📞 {phone} → 🌐 {proxy}").classes(
        "w-fit text-[11px] px-1.5 py-0.5 rounded bg-amber-50 text-amber-800",
    ).tooltip("Страна номера не совпадает со страной прокси")


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


def _render_card(ctx: _BoardContext, card: WarmingAccountState) -> None:  # pragma: no cover
    pulse = " tb-active" if card.state == "active" else ""
    element = (
        ui.card()
        .props("draggable")
        .classes(
            f"w-full p-3 gap-1 cursor-grab bg-white border border-slate-200 rounded-md{pulse}",
        )
    )
    element.on("dragstart", lambda aid=card.account_id: ctx.drag.update(account_id=aid))
    with element:
        with ui.row().classes("w-full items-center gap-2"):
            ui.element("div").classes(
                f"w-2.5 h-2.5 rounded-full {_HEALTH_DOT.get(card.health, 'bg-slate-400')}",
            )
            ui.label(card.label).classes("text-sm font-medium truncate flex-1")
            ui.label(_STATE_LABEL.get(card.state, card.state)).classes(
                f"text-[11px] px-2 py-0.5 rounded {_STATE_BADGE.get(card.state, '')}",
            )
            if card.trust_score is not None:
                _render_trust_badge(card)
        _render_trust_reasons(card)
        _render_geo_chip(card)
        _render_spam_badge(ctx, card)
        meta = f"циклов {card.cycles_completed}"
        if card.last_event:
            meta = f"{meta} · {card.last_event}"
        ui.label(meta).classes("text-[11px] text-slate-500 truncate")
        _render_card_stats(card, ctx.max_daily)
        _render_flood_wait_line(card)
        _render_quarantine_line(card)
        _render_error_line(card)
        if card.readiness and not card.readiness.ready:
            reasons = ", ".join(_ru_reason(reason) for reason in card.readiness.reasons)
            ui.label(f"не готов: {reasons}").classes("text-[11px] text-red-600 truncate")
