"""Static styling for the warming board — chips, badges, colour ramps.

Pure data tables. Extracted from ``_board`` so the rendering module stays
under the aislop file-length cap. UI-thin and excluded from coverage along
with the rest of ``features/warming``.
"""

from __future__ import annotations

from typing import Literal

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
