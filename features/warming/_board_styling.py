"""Static styling for the warming board — chips, badges, colour ramps.

Pure data tables. Extracted from ``_board`` so the rendering module stays
under the aislop file-length cap. UI-thin and excluded from coverage along
with the rest of ``features/warming``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

NotifyType = Literal["positive", "negative", "warning", "info", "ongoing"]


def _relative_eta(iso: str | None) -> str | None:  # pragma: no cover
    """Human ETA from now to an ISO timestamp, e.g. ``7 ч`` / ``12 мин``.

    Lives here so it can be reused by sibling UI renderers (the pipeline, the
    board card stats, the sleep step tooltip) without each one reaching across
    the package. ``_board`` re-exports it for backward compatibility with
    existing tests that import it from there.
    """
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
        # Sub-minute reads as "<1 мин", not a misleading "0 мин".
        return "<1 мин" if delta < 60 else f"{int(delta // 60)} мин"  # noqa: PLR2004
    if delta < _ETA_DAY_SECONDS:
        return f"{int(delta // _ETA_HOUR_SECONDS)} ч"
    return f"{int(delta // _ETA_DAY_SECONDS)} д"


_BOARD_POLL_SECONDS = 4.0
_ETA_HOUR_SECONDS = 3600
_ETA_DAY_SECONDS = 86_400

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
    "spam limited": "спам-ограничение",
    "trust critical": "низкий trust",
}

_SUMMARY_CHIPS = (
    ("Всего", "total", "bg-slate-100 text-slate-700"),
    ("Прогрев", "warming", "bg-green-100 text-green-700"),
    ("Готовы", "ready", "bg-emerald-100 text-emerald-700"),
    ("Внимание", "attention", "bg-orange-100 text-orange-700"),
    ("⛨ здоровы", "trust_healthy", "bg-green-100 text-green-700"),
    ("⛨ наблюдение", "trust_watch", "bg-amber-100 text-amber-700"),
    ("⛨ риск", "trust_risk", "bg-red-100 text-red-700"),
)

_PHASE_BAR_FILL = {
    "intro": "bg-green-500",
    "settling": "bg-sky-500",
    "warming": "bg-amber-500",
    "active": "bg-indigo-500",
    "warmed": "bg-emerald-500",
}

# Pipeline rail — per-step circle + per-connector bar classes. The actual
# colours/animations live in ``features/warming/__init__.py`` under
# ``ui.add_css(shared=True)`` so the keyframes (``tb-flow``,
# ``tb-step-spin``) and the active-state ring stay in one place. The semantic
# names here let ``_pipeline.py`` pick a class by current step state without
# hardcoding colours at the call site.
_PIPELINE_STEP_DONE = "bg-green-500 text-white"
_PIPELINE_STEP_ACTIVE = (
    "bg-indigo-600 text-white ring-4 ring-indigo-200 animate-pulse shadow-md shadow-indigo-200"
)
_PIPELINE_STEP_PENDING = "bg-slate-200 text-slate-400"
_PIPELINE_STEP_ERROR = "bg-red-500 text-white"
_PIPELINE_STEP_FLOOD = "bg-amber-500 text-white"
_PIPELINE_STEP_QUAR = "bg-orange-500 text-white"
_PIPELINE_STEP_SLEEP = "bg-blue-400 text-white ring-4 ring-blue-100"
_PIPELINE_CONNECTOR_DONE = "bg-green-400"
_PIPELINE_CONNECTOR_ACTIVE = "tb-flow-line"  # defined in __init__.py _PIPELINE_CSS
_PIPELINE_CONNECTOR_PENDING = "bg-slate-200"

# ── Card stripe colour by state ───────────────────────────────────────────────
_STRIPE_CLS: dict[str, str] = {
    "active": "bg-green-500",
    "sleeping": "bg-amber-400",
    "flood_wait": "bg-amber-400",
    "quarantine": "bg-orange-500",
    "error": "bg-red-500",
    "idle": "bg-slate-200",
}

# ── Trust score display (bare number + coloured label, no badge bg) ────────────
_TRUST_COLOR: dict[str, str] = {
    "excellent": "text-green-600",
    "good": "text-green-600",
    "watch": "text-amber-600",
    "at_risk": "text-red-600",
    "critical": "text-red-600",
}
_TRUST_LABEL_RU: dict[str, str] = {
    "excellent": "Trust — норма",
    "good": "Trust — норма",
    "watch": "Trust — внимание",
    "at_risk": "Trust — риск",
    "critical": "Trust — риск",
}

# ── Health-check rectangular chips ────────────────────────────────────────────
_CHECK_CHIP: dict[str, str] = {
    "ok": "bg-green-50 border-green-200 text-green-800",
    "warn": "bg-amber-50 border-amber-200 text-amber-800",
    "fail": "bg-red-50   border-red-200   text-red-800",
}
_CHECK_CHIP_DOT: dict[str, str] = {
    "ok": "bg-green-500",
    "warn": "bg-amber-500",
    "fail": "bg-red-500",
}

# ── Phase chip (solid fill, rounded) ─────────────────────────────────────────
_PHASE_CHIP_SOLID: dict[str, str] = {
    "intro": "bg-green-100  text-green-800",
    "settling": "bg-amber-100  text-amber-800",
    "warming": "bg-blue-100   text-blue-800",
    "active": "bg-indigo-100 text-indigo-800",
    "warmed": "bg-emerald-100 text-emerald-800",
}

# ── Status line (dot colour + dynamic text lookup) ────────────────────────────
_STATUS_DOT: dict[str, str] = {
    "active": "bg-green-500",
    "sleeping": "bg-amber-400",
    "flood_wait": "bg-amber-500",
    "quarantine": "bg-orange-500",
    "error": "bg-red-500",
}
_STATUS_ACTION_LABEL: dict[str, str] = {
    "set_online": "устанавливает онлайн",
    "join": "вступает в канал",
    "read": "читает каналы",
    "react": "ставит реакции",
    "read_or_react": "ставит реакции",
    "send_dm": "отправляет сообщение",
}

# ── Detail panel icon containers (28px squares) ───────────────────────────────
# Maps `kind` string → (icon_bg_classes, icon_color_classes)
_DETAIL_ICON_THEME: dict[str, tuple[str, str]] = {
    "active": ("bg-blue-100", "text-blue-600"),
    "sleep": ("bg-slate-100", "text-slate-500"),
    "flood": ("bg-amber-100", "text-amber-600"),
    "quar": ("bg-orange-100", "text-orange-600"),
    "error": ("bg-red-100", "text-red-600"),
}
