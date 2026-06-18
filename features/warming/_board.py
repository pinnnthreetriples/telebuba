"""Kanban board rendering — summary chips, drag columns, account cards.

UI-thin per non-negotiable #1; every function is exercised manually and excluded
from coverage (``pragma: no cover``). The logic it calls is unit-tested in
``services.warming``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from nicegui import ui

from schemas.warming import StartWarmingRequest, StopWarmingRequest
from services.spam_status import refresh_spam_status
from services.warming import WarmingNotReadyError, start_warming, stop_warming

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Callable

    from schemas.warming import WarmingAccountState, WarmingBoardState, WarmingSummary

NotifyType = Literal["positive", "negative", "warning", "info", "ongoing"]

_BOARD_POLL_SECONDS = 4.0
_ETA_HOUR_SECONDS = 3600
_ETA_DAY_SECONDS = 86_400
_ERROR_MAX_LEN = 80

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
# classify_spam_probe writes this exact phrase to ``account_spam_status.detail``
# when @SpamBot replies "being checked" — we recognise it to distinguish a
# Telegram-side review from a probe error.
_SPAM_DETAIL_BEING_CHECKED = "account is being checked"
_SPAM_OUTCOME_BRIEF_MAX = 80

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

# Visual treatment for the per-check chips: dot colour + label colour.
_CHECK_DOT = {
    "ok": "bg-green-500",
    "warn": "bg-amber-500",
    "fail": "bg-red-500",
}
_CHECK_TEXT = {
    "ok": "text-slate-600",
    "warn": "text-amber-700",
    "fail": "text-red-700",
}


def _ru_reason(reason: str) -> str:  # pragma: no cover
    if reason in _READINESS_REASON_RU:
        return _READINESS_REASON_RU[reason]
    if reason.startswith("session "):
        return f"сессия: {reason[len('session ') :]}"
    return reason


def _spam_badge_label(status: str, detail: str | None) -> str:
    """Russian badge text, distinguishing unknown sub-states.

    A probe-side error and a Telegram-side "being checked" both land in the
    same ``unknown`` status, but they mean very different things — the badge
    text now reflects that so an operator sees at a glance what happened
    instead of having to hover.
    """
    if status == "clean":
        return "Спам: чисто"
    if status == "limited":
        return "Спам: ограничен"
    if detail == _SPAM_DETAIL_BEING_CHECKED:
        return "Спам: на проверке Telegram"
    if detail:
        return "Спам: ошибка проверки"
    return "Спам: не проверен"


def _spam_badge_classes(status: str, detail: str | None) -> str:
    """Tailwind colour pair for the spam badge — amber for an actual probe error."""
    if status == "clean":
        return "text-green-700 bg-green-100"
    if status == "limited":
        return "text-red-700 bg-red-100"
    if detail and detail != _SPAM_DETAIL_BEING_CHECKED:
        return "text-amber-700 bg-amber-100"
    return "text-slate-600 bg-slate-100"


def _spam_tooltip(status: str, detail: str | None) -> str:
    """Full tooltip — the *why* behind the badge, always populated."""
    if status == "clean":
        return "@SpamBot: ограничений нет"
    if status == "limited":
        return detail or "@SpamBot: аккаунт ограничен"
    if detail == _SPAM_DETAIL_BEING_CHECKED:
        return "Telegram сам проверяет аккаунт — повторите позже"
    if detail:
        return f"Проверка не прошла: {detail}"
    return "@SpamBot ещё не запрашивался — нажмите «проверить»"


def _spam_outcome_label(status: str, detail: str | None) -> str:
    """Brief Russian phrase for the post-refresh toast."""
    if status == "clean":
        return "чисто"
    if status == "limited":
        return f"ограничен ({detail})" if detail else "ограничен"
    if detail == _SPAM_DETAIL_BEING_CHECKED:
        return "Telegram сам проверяет аккаунт"
    if detail:
        brief = (
            detail
            if len(detail) <= _SPAM_OUTCOME_BRIEF_MAX
            else detail[: _SPAM_OUTCOME_BRIEF_MAX - 1] + "…"
        )
        return f"проверка не прошла — {brief}"
    return "вердикт не получен"


def _spam_notify_type(status: str, detail: str | None) -> NotifyType:
    """Toast colour: positive for clean, negative for limited, warning on probe error."""
    if status == "clean":
        return "positive"
    if status == "limited":
        return "negative"
    if detail and detail != _SPAM_DETAIL_BEING_CHECKED:
        return "warning"
    return "info"


def _check_states(card: WarmingAccountState) -> list[tuple[str, str, str]]:
    """Derive seven UI health checks from card fields and trust_reasons.

    Returns a list of ``(label, status, tooltip)`` triples, where ``status`` is
    ``"ok" | "warn" | "fail"``. Pure — no I/O, no UI side-effects — so the
    list is straightforward to unit-test. The card already carries every
    field we read here; we just invert the trust-model's "what's wrong"
    view into the operator-facing "what's checked and how it's doing".
    """
    reasons = set(card.trust_reasons)
    return [
        _check_session(reasons),
        _check_spam(card),
        _check_simple(reasons, "proxy failed", "прокси", "прокси не работает", "прокси работает"),
        _check_geo(card, reasons),
        _check_new_account(reasons),
        _check_simple(
            reasons,
            "recent flood",
            "flood",
            "активный flood-wait",
            "flood-wait не активен",
        ),
        _check_quarantine(card),
    ]


def _check_session(reasons: set[str]) -> tuple[str, str, str]:
    session_bad = next((r for r in reasons if r.startswith("status ")), None)
    if session_bad:
        return ("сессия", "fail", f"сессия: {session_bad[len('status ') :]}")
    return ("сессия", "ok", "сессия живая")


def _check_spam(card: WarmingAccountState) -> tuple[str, str, str]:
    """None and "unknown" both map to warn (data missing, not a risk)."""
    status = card.spam_status or "unknown"
    tooltip = _spam_tooltip(status, card.spam_detail)
    if status == "clean":
        return ("@SpamBot", "ok", tooltip)
    if status == "limited":
        return ("@SpamBot", "fail", tooltip)
    return ("@SpamBot", "warn", tooltip)


def _check_simple(
    reasons: set[str],
    reason_key: str,
    label: str,
    fail_tip: str,
    ok_tip: str,
) -> tuple[str, str, str]:
    """Generic 2-state check driven by a single reason key."""
    if reason_key in reasons:
        return (label, "fail", fail_tip)
    return (label, "ok", ok_tip)


def _check_geo(card: WarmingAccountState, reasons: set[str]) -> tuple[str, str, str]:
    """Geo verdict + tooltip with the specific country pair when known."""
    if "geo mismatch" in reasons:
        if card.phone_country and card.proxy_country:
            tip = f"📞 {card.phone_country} → 🌐 {card.proxy_country}: страны не совпадают"
        else:
            tip = "страна номера ≠ страна прокси"
        return ("гео", "fail", tip)
    if "geo unknown" in reasons:
        return ("гео", "warn", "страна номера или прокси не определена")
    if card.phone_country and card.proxy_country:
        return ("гео", "ok", f"страны совпадают ({card.phone_country})")
    return ("гео", "ok", "проверка пройдена")


def _check_new_account(reasons: set[str]) -> tuple[str, str, str]:
    """Partial signal — warn rather than fail (account is still usable)."""
    if "new account" in reasons:
        return ("возраст", "warn", "новый аккаунт (< 48 ч)")
    return ("возраст", "ok", "возраст ≥ 48 ч")


def _check_quarantine(card: WarmingAccountState) -> tuple[str, str, str]:
    q = card.quarantine_count
    if q > 0:
        return ("карантин", "fail", f"карантинов: {q}")
    return ("карантин", "ok", "карантинов нет")


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
