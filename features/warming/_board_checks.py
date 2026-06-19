"""Per-account check formatters and spam-status helpers for the warming board.

Pure functions — no I/O, no UI side-effects. Translate the English domain
labels produced by ``services.warming`` into the Russian operator-facing
strings the cards render, and invert the trust-model's "what's wrong" view
into the seven-chip "what's checked" view.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.config import settings
from features.warming._board_styling import (
    _READINESS_REASON_RU,
    _SPAM_DETAIL_BEING_CHECKED,
    _SPAM_OUTCOME_BRIEF_MAX,
    NotifyType,
)

if TYPE_CHECKING:
    from schemas.warming import WarmingAccountState


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
    text now reflects that so an operator sees at a glance what happened.
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


_TWO_DAYS_IN_HOURS = 48


def _format_new_account_threshold(hours: float) -> str:
    """Pure: format the new-account cutoff for the chip tooltip.

    Reads naturally as «< 48 ч» up to two days, then folds into days
    («< 7 д») so the operator does not see «< 168 ч».
    """
    if hours <= 0:
        return "< 0 ч"
    int_hours = int(hours)
    if int_hours < _TWO_DAYS_IN_HOURS:
        return f"< {int_hours} ч"
    return f"< {int_hours // 24} д"


def _check_states(
    card: WarmingAccountState,
    *,
    now: datetime | None = None,
) -> list[tuple[str, str, str]]:
    """Derive the UI health-check chips from a card.

    Pure modulo ``datetime.now()`` (overridable via ``now`` for tests).
    Returns ``(label, status, tooltip)`` triples where ``status`` is
    ``"ok" | "warn" | "fail"``.
    """
    reasons = set(card.trust_reasons)
    readiness_reasons = set(card.readiness.reasons) if card.readiness else set()
    new_account_hours = settings.trust.new_account_hours
    current_now = now if now is not None else datetime.now(UTC)
    return [
        _check_session(reasons),
        _check_spam(card),
        _check_proxy(reasons, readiness_reasons),
        _check_geo(card, reasons),
        _check_new_account(reasons, new_account_hours),
        _check_flood(card, reasons, current_now),
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


def _check_proxy(
    trust_reasons: set[str],
    readiness_reasons: set[str],
) -> tuple[str, str, str]:
    """Tri-state proxy chip.

    The trust model only flags ``"proxy failed"`` (proxy exists but probe
    failed); readiness separately tracks ``"no proxy"`` (no host configured
    at all). The chip must honour both so an account without a proxy can
    not silently render green «прокси работает».
    """
    if "no proxy" in readiness_reasons:
        return ("прокси", "fail", "нет прокси")
    if "proxy failed" in trust_reasons:
        return ("прокси", "fail", "прокси не работает")
    return ("прокси", "ok", "прокси работает")


def _check_geo(card: WarmingAccountState, reasons: set[str]) -> tuple[str, str, str]:
    """Geo verdict.

    «geo mismatch» in trust → fail; «geo unknown» → warn. When neither
    appears but one of the countries is still missing, downgrade to warn —
    a silent ok in that case was the previous bug.
    """
    if "geo mismatch" in reasons:
        if card.phone_country and card.proxy_country:
            tip = f"📞 {card.phone_country} → 🌐 {card.proxy_country}: страны не совпадают"
        else:
            tip = "страна номера ≠ страна прокси"
        return ("гео", "fail", tip)
    if "geo unknown" in reasons:
        return ("гео", "warn", "страна номера или прокси не определена")
    if not card.phone_country or not card.proxy_country:
        return ("гео", "warn", "страна номера или прокси не определена")
    return ("гео", "ok", f"страны совпадают ({card.phone_country})")


def _check_new_account(
    reasons: set[str],
    new_account_hours: float,
) -> tuple[str, str, str]:
    """Partial signal — warn rather than fail (account is still usable).

    Tooltip uses the configured threshold from ``settings.trust.new_account_hours``
    rather than a hard-coded «48 ч» so the chip stays honest if the cutoff
    moves.
    """
    threshold_label = _format_new_account_threshold(new_account_hours)
    if "new account" in reasons:
        return ("возраст", "warn", f"новый аккаунт ({threshold_label})")
    return ("возраст", "ok", f"возраст ≥ {threshold_label.lstrip('< ')}")


def _check_flood(
    card: WarmingAccountState,
    reasons: set[str],
    now: datetime,
) -> tuple[str, str, str]:
    """Flood-wait chip — live state, not stale trust_reasons.

    The trust «recent flood» reason is built once per board poll; the card
    body checks ``card.state == "flood_wait"`` directly. To keep the chip
    in sync with the body, evaluate the live signal first (state pill or
    unexpired ``flood_wait_until``), then fall back to the trust reason
    for the recent-but-not-active case.
    """
    if card.state == "flood_wait":
        return ("flood", "fail", "активный flood-wait")
    if card.flood_wait_until:
        try:
            until = datetime.fromisoformat(card.flood_wait_until)
        except ValueError:
            until = None
        if until is not None:
            if until.tzinfo is None:
                until = until.replace(tzinfo=UTC)
            if until > now:
                return ("flood", "fail", "активный flood-wait")
    if "recent flood" in reasons:
        return ("flood", "warn", "недавний flood-wait")
    return ("flood", "ok", "flood-wait не активен")


def _check_quarantine(card: WarmingAccountState) -> tuple[str, str, str]:
    """Internal workflow signal — not a Telegram trust signal.

    Labelled «карантин (внутр.)» to remind the operator this is the
    runtime peer-flood cooldown counter, not an account-level ban
    indicator. Telegram does not see this number.
    """
    q = card.quarantine_count
    if q > 0:
        return ("карантин (внутр.)", "fail", f"карантинов: {q}")
    return ("карантин (внутр.)", "ok", "карантинов нет")
