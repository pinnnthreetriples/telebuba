"""Tests for the pure helpers behind the warming kanban card.

The render functions themselves are UI-thin and excluded from coverage; we
exercise the data-shaping helpers: ``_check_states`` (the seven-signal
"positive signals" derivation shown under the trust badge) and
``_card_signature`` (which controls when the per-card poll-driven refresh
fires — a missed field means a stale card, an over-broad one means flicker).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from features.warming._board import _card_signature, _relative_eta
from features.warming._board_checks import (
    _check_flood,
    _check_proxy,
    _check_states,
    _format_new_account_threshold,
    _ru_event,
    _ru_plural,
    _ru_reason,
    _spam_badge_classes,
    _spam_badge_label,
    _spam_notify_type,
    _spam_outcome_label,
    _spam_tooltip,
)
from features.warming._channels import _count_submitted_lines
from schemas.warming import WarmingAccountState, WarmingReadiness


def _base_card() -> WarmingAccountState:
    return WarmingAccountState(account_id="acc-1", label="Acc 1", state="idle", health="idle")


def _by_label(checks: list[tuple[str, str, str]]) -> dict[str, tuple[str, str]]:
    return {label: (status, tooltip) for label, status, tooltip in checks}


def test_check_states_healthy_card_is_all_ok() -> None:
    card = _base_card().model_copy(
        update={
            "trust_score": 100,
            "trust_band": "excellent",
            "trust_reasons": [],
            "spam_status": "clean",
            "quarantine_count": 0,
            "phone_country": "RU",
            "proxy_country": "RU",
            "readiness": WarmingReadiness(ready=True, reasons=[]),
        },
    )

    checks = _check_states(card)

    labels = [c[0] for c in checks]
    assert labels == [
        "сессия",
        "прокси",
        "гео",
        "возраст",
        "flood",
        "карантин (внутр.)",
    ]
    assert all(status == "ok" for _, status, _ in checks)


def test_check_states_geo_mismatch_carries_country_pair_in_tooltip() -> None:
    card = _base_card().model_copy(
        update={
            "trust_reasons": ["geo mismatch"],
            "phone_country": "CO",
            "proxy_country": "NL",
        },
    )

    geo_status, geo_tip = _by_label(_check_states(card))["гео"]

    assert geo_status == "fail"
    assert "CO" in geo_tip
    assert "NL" in geo_tip


def test_check_states_geo_unknown_is_warn() -> None:
    card = _base_card().model_copy(update={"trust_reasons": ["geo unknown"]})

    geo_status, _ = _by_label(_check_states(card))["гео"]

    assert geo_status == "warn"


def test_check_states_quarantine_history_is_warn_not_fail() -> None:
    # q>0 but not *currently* quarantined → history, not an active blocker (audit П10):
    # a restarted account must not look blocked by a past peer-flood counter.
    card = _base_card().model_copy(update={"quarantine_count": 3})  # _base_card state="idle"

    q_status, q_tip = _by_label(_check_states(card))["карантин (внутр.)"]

    assert q_status == "warn"
    assert "история" in q_tip
    assert "3" in q_tip


def test_check_states_active_quarantine_is_fail() -> None:
    card = _base_card().model_copy(update={"state": "quarantine", "quarantine_count": 1})

    q_status, q_tip = _by_label(_check_states(card))["карантин (внутр.)"]

    assert q_status == "fail"
    assert "актив" in q_tip


def test_check_states_session_failure_propagates_reason_to_tooltip() -> None:
    card = _base_card().model_copy(update={"trust_reasons": ["status banned"]})

    session_status, session_tip = _by_label(_check_states(card))["сессия"]

    assert session_status == "fail"
    assert "banned" in session_tip


def test_check_states_new_account_is_warn_not_fail() -> None:
    card = _base_card().model_copy(update={"trust_reasons": ["new account"]})

    age_status, _ = _by_label(_check_states(card))["возраст"]

    assert age_status == "warn"


# --- spam helper functions (used by badge / chip / toast) ----------------------


def test_spam_badge_label_distinguishes_unknown_sub_states() -> None:
    assert _spam_badge_label("clean", None) == "Спам: чисто"
    assert _spam_badge_label("limited", None) == "Спам: ограничен"
    assert _spam_badge_label("unknown", None) == "Спам: не проверен"
    assert _spam_badge_label("unknown", "account is being checked") == "Спам: на проверке Telegram"
    assert _spam_badge_label("unknown", "TimeoutError: x") == "Спам: ошибка проверки"


def test_spam_badge_classes_use_amber_for_probe_error() -> None:
    """A real probe error should look different from a never-probed account."""
    error_cls = _spam_badge_classes("unknown", "TimeoutError: x")
    no_probe_cls = _spam_badge_classes("unknown", None)
    being_checked_cls = _spam_badge_classes("unknown", "account is being checked")

    assert "amber" in error_cls
    assert "amber" not in no_probe_cls
    assert "amber" not in being_checked_cls


def test_spam_tooltip_always_populated() -> None:
    assert _spam_tooltip("clean", None) != ""
    assert _spam_tooltip("limited", None) != ""
    assert _spam_tooltip("unknown", None) != ""
    assert "TimeoutError" in _spam_tooltip("unknown", "TimeoutError: timed out")
    assert "Telegram" in _spam_tooltip("unknown", "account is being checked")
    # ``limited`` prefers spam_detail (e.g. "until 2026-08-12") over the fallback.
    assert _spam_tooltip("limited", "until 2026-08-12") == "until 2026-08-12"


def test_spam_outcome_label_for_toast() -> None:
    assert _spam_outcome_label("clean", None) == "чисто"
    assert _spam_outcome_label("limited", None) == "ограничен"
    assert _spam_outcome_label("limited", "until 2026-08-12") == "ограничен (until 2026-08-12)"
    assert _spam_outcome_label("unknown", None) == "вердикт не получен"
    assert "Telegram" in _spam_outcome_label("unknown", "account is being checked")

    err = _spam_outcome_label("unknown", "TimeoutError: timed out")
    assert err.startswith("проверка не прошла")
    assert "TimeoutError" in err


def test_spam_outcome_label_truncates_long_errors() -> None:
    """A 200-char traceback shouldn't dominate the toast."""
    long_detail = "ConnectionError: " + ("very long stack trace " * 30)

    label = _spam_outcome_label("unknown", long_detail)

    assert label.endswith("…")
    assert len(label) < len(long_detail)


def test_spam_notify_type_matches_severity() -> None:
    assert _spam_notify_type("clean", None) == "positive"
    assert _spam_notify_type("limited", None) == "negative"
    assert _spam_notify_type("unknown", "TimeoutError: x") == "warning"
    assert _spam_notify_type("unknown", "account is being checked") == "info"
    assert _spam_notify_type("unknown", None) == "info"


def test_card_signature_reacts_to_last_error() -> None:
    base = _base_card()
    changed = base.model_copy(update={"last_error": "PEER_FLOOD"})
    assert _card_signature(base) != _card_signature(changed)


def test_card_signature_reacts_to_flood_wait_until() -> None:
    base = _base_card()
    changed = base.model_copy(update={"flood_wait_until": "2026-06-20T00:00:00+00:00"})
    assert _card_signature(base) != _card_signature(changed)


def test_card_signature_reacts_to_quarantine_count() -> None:
    base = _base_card()
    changed = base.model_copy(update={"quarantine_count": 2})
    assert _card_signature(base) != _card_signature(changed)


def test_card_signature_reacts_to_trust_reasons() -> None:
    base = _base_card().model_copy(
        update={"trust_score": 80, "trust_band": "good", "trust_reasons": ["geo mismatch"]},
    )
    changed = base.model_copy(update={"trust_reasons": ["geo mismatch", "new account"]})
    assert _card_signature(base) != _card_signature(changed)


def test_card_signature_reacts_to_geo_pair() -> None:
    base = _base_card().model_copy(update={"phone_country": "RU", "proxy_country": "RU"})
    changed = base.model_copy(update={"proxy_country": "DE"})
    assert _card_signature(base) != _card_signature(changed)


def test_card_signature_reacts_to_spam_detail() -> None:
    base = _base_card().model_copy(update={"spam_status": "limited"})
    changed = base.model_copy(update={"spam_detail": "until 2026-08-12"})
    assert _card_signature(base) != _card_signature(changed)


def test_card_signature_stable_when_nothing_changes() -> None:
    card = _base_card().model_copy(
        update={"trust_score": 80, "trust_band": "good", "trust_reasons": ["geo mismatch"]},
    )
    assert _card_signature(card) == _card_signature(card)


# --- _check_proxy tri-state -----------------------------------------------


def test_check_proxy_no_proxy_is_fail() -> None:
    label, status, tooltip = _check_proxy(set(), {"no proxy"})
    assert label == "прокси"
    assert status == "fail"
    assert "нет" in tooltip


def test_check_proxy_failed_is_fail() -> None:
    _, status, tooltip = _check_proxy({"proxy failed"}, set())
    assert status == "fail"
    assert "не работает" in tooltip


def test_check_proxy_ok_otherwise() -> None:
    _, status, tooltip = _check_proxy(set(), set())
    assert status == "ok"
    assert tooltip == "прокси работает"


def test_check_proxy_no_proxy_wins_over_proxy_failed() -> None:
    # Readiness's "no proxy" is the more informative signal.
    _, status, tooltip = _check_proxy({"proxy failed"}, {"no proxy"})
    assert status == "fail"
    assert "нет" in tooltip


# --- _check_flood reads live state ----------------------------------------


_NOW = datetime(2026, 6, 19, 12, 0, 0, tzinfo=UTC)
_FUTURE = "2026-06-19T13:00:00+00:00"
_PAST = "2026-06-19T11:00:00+00:00"


def test_check_flood_state_pill_takes_priority() -> None:
    card = _base_card().model_copy(update={"state": "flood_wait"})
    _, status, tooltip = _check_flood(card, set(), _NOW)
    assert status == "fail"
    assert "активный" in tooltip


def test_check_flood_unexpired_until_is_fail() -> None:
    card = _base_card().model_copy(update={"flood_wait_until": _FUTURE})
    _, status, _ = _check_flood(card, set(), _NOW)
    assert status == "fail"


def test_check_flood_expired_until_falls_through_to_ok() -> None:
    card = _base_card().model_copy(update={"flood_wait_until": _PAST})
    _, status, _ = _check_flood(card, set(), _NOW)
    assert status == "ok"


def test_check_flood_recent_reason_is_warn_not_fail() -> None:
    card = _base_card()
    _, status, _ = _check_flood(card, {"recent flood"}, _NOW)
    assert status == "warn"


def test_check_flood_clean_is_ok() -> None:
    card = _base_card()
    _, status, _ = _check_flood(card, set(), _NOW)
    assert status == "ok"


# --- _check_geo warns on missing data --------------------------------------


def test_check_states_geo_missing_countries_is_warn() -> None:
    card = _base_card().model_copy(
        update={"phone_country": None, "proxy_country": None, "trust_reasons": []},
    )
    geo_status, _ = _by_label(_check_states(card))["гео"]
    assert geo_status == "warn"


# --- threshold formatter --------------------------------------------------


def test_format_new_account_threshold_hours_under_two_days() -> None:
    assert _format_new_account_threshold(24.0) == "< 24 ч"
    assert _format_new_account_threshold(36.0) == "< 36 ч"


def test_format_new_account_threshold_days_when_long() -> None:
    assert _format_new_account_threshold(48.0) == "< 2 д"
    assert _format_new_account_threshold(168.0) == "< 7 д"


# --- proxy chip flips on a real card scenario -----------------------------


def test_check_states_proxy_chip_fails_on_no_proxy_via_readiness() -> None:
    """Card with no proxy attached must render the chip as fail, not ok."""
    card = _base_card().model_copy(
        update={
            "trust_reasons": [],
            "readiness": WarmingReadiness(ready=False, reasons=["no proxy", "no channels"]),
        },
    )

    proxy_status, proxy_tip = _by_label(_check_states(card))["прокси"]

    assert proxy_status == "fail"
    assert "нет" in proxy_tip


def test_check_states_intro_phase_keeps_age_chip_amber() -> None:
    # 48-72h account: the trust "new account" penalty has aged off but the phase
    # is still intro (72h floor). The возраст chip stays amber so it does not
    # contradict the «Новый» phase chip (#98).
    card = _base_card().model_copy(update={"phase": "intro", "trust_reasons": []})
    age_status, _ = _by_label(_check_states(card))["возраст"]
    assert age_status == "warn"


def test_check_states_settled_phase_age_chip_is_ok() -> None:
    card = _base_card().model_copy(update={"phase": "settling", "trust_reasons": []})
    age_status, _ = _by_label(_check_states(card))["возраст"]
    assert age_status == "ok"


# --- audit #101: Russian wording helpers --------------------------------------


@pytest.mark.parametrize(
    ("count", "form"),
    [(1, "цикл"), (2, "цикла"), (4, "цикла"), (5, "циклов"), (11, "циклов"), (21, "цикл")],
)
def test_ru_plural_cycle_forms(count: int, form: str) -> None:
    assert _ru_plural(count, ("цикл", "цикла", "циклов")) == form


def test_ru_event_translates_known_cycle_and_unknown_tokens() -> None:
    assert _ru_event("daily_limit") == "дневной лимит"
    assert _ru_event("cycle_not_ready") == "не готов к циклу"  # П3 park event (audit review)
    assert _ru_event("cycle:ok") == "цикл выполнен"
    assert _ru_event("cycle:weird") == "цикл: weird"  # unknown status keeps the suffix
    assert _ru_event("totally_unknown") == "totally_unknown"  # falls back to the raw token


def test_relative_eta_sub_minute_reads_less_than_one_minute() -> None:
    soon = (datetime.now(UTC) + timedelta(seconds=30)).isoformat()
    assert _relative_eta(soon) == "<1 мин"


def test_relative_eta_past_reads_now() -> None:
    past = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
    assert _relative_eta(past) == "сейчас"


def test_count_submitted_lines_counts_space_separated() -> None:
    # The service splits on [\s,]+; the UI counter must match so «пропущено» is
    # not under-reported for space-separated input (#102).
    assert _count_submitted_lines("@a @b @c") == 3
    assert _count_submitted_lines("@a, @b\n@c") == 3
    assert _count_submitted_lines("   ") == 0


# --- readiness reason translation: every reason gets a Russian rendering ----


# Every readiness reason ``services/warming/pacing.evaluate_readiness`` can
# actually emit. The list is hand-curated against the function body so a
# new reason added there shows up as a test failure here instead of a
# silent English literal in the operator's UI.
_EVERY_READINESS_REASON = [
    "no proxy",
    "proxy failed",
    "no channels",
    "spam limited",
    "trust critical",
    # ``session {status}`` — one entry per possible AccountStatus literal:
    "session new",
    "session unauthorized",
    "session session_error",
    "session account_error",
    "session flood_wait",
    "session network_error",
    "session proxy_error",
    "session unknown_error",
]


@pytest.mark.parametrize("reason", _EVERY_READINESS_REASON)
def test_ru_reason_translates_every_emitted_reason(reason: str) -> None:
    """``_ru_reason`` must never return the English literal it was given."""
    translated = _ru_reason(reason)
    assert translated != reason, f"reason {reason!r} fell through untranslated"
    assert any(cyr in translated for cyr in "абвгдежзиклмнопрстуфхцчшщэюя"), (
        f"translation of {reason!r} contains no Cyrillic letters: {translated!r}"
    )
