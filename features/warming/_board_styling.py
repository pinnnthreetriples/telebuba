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

# Phase chip styling — semantic colour ramp (slate → sky → amber → lime →
# emerald). The chip's bg/text/ring trio matches; the progress bar reuses the
# ``-500`` tone for fill.
_PHASE_CHIP_CLASSES = {
    "intro": "bg-slate-100 text-slate-700 ring-slate-200",
    "settling": "bg-sky-50 text-sky-800 ring-sky-200",
    "warming": "bg-amber-50 text-amber-800 ring-amber-200",
    "active": "bg-lime-50 text-lime-800 ring-lime-200",
    "warmed": "bg-emerald-50 text-emerald-800 ring-emerald-200",
}
_PHASE_BAR_FILL = {
    "intro": "bg-slate-400",
    "settling": "bg-sky-500",
    "warming": "bg-amber-500",
    "active": "bg-lime-500",
    "warmed": "bg-emerald-500",
}

# Pipeline rail — per-step circle + per-connector bar classes. The actual
# colours/animations live in ``features/warming/__init__.py`` under
# ``ui.add_css(shared=True)`` so the keyframes (``tb-flow``,
# ``tb-step-spin``) and the active-state ring stay in one place. The semantic
# names here let ``_pipeline.py`` pick a class by current step state without
# hardcoding colours at the call site.
_PIPELINE_STEP_DONE = "tb-step-done"
_PIPELINE_STEP_ACTIVE = "tb-step-active"
_PIPELINE_STEP_PENDING = "tb-step-pending"
_PIPELINE_STEP_ERROR = "tb-step-error"
_PIPELINE_STEP_FLOOD = "tb-step-flood"
_PIPELINE_STEP_QUAR = "tb-step-quar"
_PIPELINE_CONNECTOR_DONE = "tb-connector-done"
_PIPELINE_CONNECTOR_ACTIVE = "tb-connector-active"
_PIPELINE_CONNECTOR_PENDING = "tb-connector-pending"
