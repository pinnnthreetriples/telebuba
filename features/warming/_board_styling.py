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
# Status mini-pills — spec C.3 token map (statusMap + warming-card states).
# Each value is a feature class defined in ``__init__.py`` carrying the exact
# spec bg/text hex pair (e.g. complete #12A150 on #DDF7E9, sleeping #E08700 on
# #FFF0D2). The renderer keeps applying these via ``.classes(...)``.
_STATE_BADGE = {
    "idle": "tbw-pill-idle",
    "active": "tbw-pill-green",
    "sleeping": "tbw-pill-amber",
    "flood_wait": "tbw-pill-amber",
    "quarantine": "tbw-pill-orange",
    "error": "tbw-pill-red",
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
    ("Всего", "total", "tbw-chip-ink"),
    ("Прогрев", "warming", "tbw-chip-green"),
    ("Готовы", "ready", "tbw-chip-green"),
    ("Внимание", "attention", "tbw-chip-orange"),
    ("⛨ здоровы", "trust_healthy", "tbw-chip-green"),
    ("⛨ наблюдение", "trust_watch", "tbw-chip-amber"),
    ("⛨ риск", "trust_risk", "tbw-chip-red"),
)

# Phase progress-bar fill — spec blue→green ramp expressed as feature classes.
_PHASE_BAR_FILL = {
    "intro": "tbw-fill-green",
    "settling": "tbw-fill-blue",
    "warming": "tbw-fill-amber",
    "active": "tbw-fill-blue",
    "warmed": "tbw-fill-green",
}

# Pipeline rail — per-step circle + per-connector bar classes. The actual
# colours/animations live in ``features/warming/__init__.py`` under
# ``ui.add_css(shared=True)`` so the keyframes (``tb-flow``,
# ``tb-step-spin``) and the active-state ring stay in one place. The semantic
# names here let ``_pipeline.py`` pick a class by current step state without
# hardcoding colours at the call site.
_PIPELINE_STEP_DONE = "tbw-step-done"
_PIPELINE_STEP_ACTIVE = "tbw-step-active tb-livedot"
_PIPELINE_STEP_PENDING = "tbw-step-pending"
_PIPELINE_STEP_ERROR = "tbw-step-error"
_PIPELINE_STEP_FLOOD = "tbw-step-flood"
_PIPELINE_STEP_QUAR = "tbw-step-quar"
_PIPELINE_STEP_SLEEP = "tbw-step-sleep"
_PIPELINE_CONNECTOR_DONE = "tbw-conn-done"
_PIPELINE_CONNECTOR_ACTIVE = "tbw-conn-active"  # defined in __init__.py _PIPELINE_CSS
_PIPELINE_CONNECTOR_PENDING = "tbw-conn-pending"

# ── Card stripe colour by state (spec status hues) ────────────────────────────
_STRIPE_CLS: dict[str, str] = {
    "active": "tbw-stripe-green",
    "sleeping": "tbw-stripe-amber",
    "flood_wait": "tbw-stripe-amber",
    "quarantine": "tbw-stripe-orange",
    "error": "tbw-stripe-red",
    "idle": "tbw-stripe-idle",
}

# ── Trust score display (bare number + coloured label, no badge bg) ────────────
# trustColor(t): t≥70 #12A150, 45≤t<70 #E08700, t<45 #E5372A (spec §C token ref).
_TRUST_COLOR: dict[str, str] = {
    "excellent": "tbw-text-green",
    "good": "tbw-text-green",
    "watch": "tbw-text-amber",
    "at_risk": "tbw-text-red",
    "critical": "tbw-text-red",
}
_TRUST_LABEL_RU: dict[str, str] = {
    "excellent": "Trust — норма",
    "good": "Trust — норма",
    "watch": "Trust — внимание",
    "at_risk": "Trust — риск",
    "critical": "Trust — риск",
}

# ── Phase chip (solid fill, rounded) ─────────────────────────────────────────
_PHASE_CHIP_SOLID: dict[str, str] = {
    "intro": "tbw-pill-green",
    "settling": "tbw-pill-amber",
    "warming": "tbw-pill-blue",
    "active": "tbw-pill-blue",
    "warmed": "tbw-pill-green",
}

# ── Status line (dot colour + dynamic text lookup) ────────────────────────────
_STATUS_DOT: dict[str, str] = {
    "active": "tbw-dot-green",
    "sleeping": "tbw-dot-amber",
    "flood_wait": "tbw-dot-amber",
    "quarantine": "tbw-dot-orange",
    "error": "tbw-dot-red",
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
# Maps `kind` string → (icon_bg_class, icon_color_class) — spec light-tint tiles.
_DETAIL_ICON_THEME: dict[str, tuple[str, str]] = {
    "active": ("tbw-tile-blue", "tbw-text-blue"),
    "sleep": ("tbw-tile-gray", "tbw-text-muted"),
    "flood": ("tbw-tile-amber", "tbw-text-amber"),
    "quar": ("tbw-tile-orange", "tbw-text-orange"),
    "error": ("tbw-tile-red", "tbw-text-red"),
}
