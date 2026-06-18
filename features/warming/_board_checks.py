"""Per-account check formatters and spam-status helpers for the warming board.

Pure functions — no I/O, no UI side-effects. Translate the English domain
labels produced by ``services.warming`` into the Russian operator-facing
strings the cards render, and invert the trust-model's "what's wrong" view
into the seven-chip "what's checked" view.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

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


def _check_states(card: WarmingAccountState) -> list[tuple[str, str, str]]:
    """Derive seven UI health checks from card fields and trust_reasons.

    Returns ``(label, status, tooltip)`` triples where ``status`` is
    ``"ok" | "warn" | "fail"``. Pure — no I/O — so straightforward to test.
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
