"""Tests for the pure helpers behind the warming kanban card.

The render functions themselves are UI-thin and excluded from coverage; we
exercise the data-shaping helpers: ``_check_states`` (the seven-signal
"positive signals" derivation shown under the trust badge) and
``_board_signature`` (which controls when the poll loop redraws — a missed
field means a stale card).
"""

from __future__ import annotations

from features.warming._board import (
    _board_signature,
    _check_states,
    _spam_badge_classes,
    _spam_badge_label,
    _spam_notify_type,
    _spam_outcome_label,
    _spam_tooltip,
)
from schemas.warming import (
    WarmingAccountState,
    WarmingBoardState,
    WarmingChannelList,
    WarmingSettings,
)


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
        },
    )

    checks = _check_states(card)

    labels = [c[0] for c in checks]
    assert labels == ["сессия", "@SpamBot", "прокси", "гео", "возраст", "flood", "карантин"]
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


def test_check_states_spam_unknown_is_warn() -> None:
    card = _base_card().model_copy(update={"spam_status": None})

    spam_status, spam_tip = _by_label(_check_states(card))["@SpamBot"]

    assert spam_status == "warn"
    assert "нажмите" in spam_tip  # tooltip hints at the action


def test_check_states_spam_limited_is_fail_with_detail() -> None:
    card = _base_card().model_copy(
        update={"spam_status": "limited", "spam_detail": "until 2026-08-12"},
    )

    spam_status, spam_tip = _by_label(_check_states(card))["@SpamBot"]

    assert spam_status == "fail"
    assert spam_tip == "until 2026-08-12"


def test_check_states_quarantine_count_in_tooltip() -> None:
    card = _base_card().model_copy(update={"quarantine_count": 3})

    q_status, q_tip = _by_label(_check_states(card))["карантин"]

    assert q_status == "fail"
    assert "3" in q_tip


def test_check_states_session_failure_propagates_reason_to_tooltip() -> None:
    card = _base_card().model_copy(update={"trust_reasons": ["status banned"]})

    session_status, session_tip = _by_label(_check_states(card))["сессия"]

    assert session_status == "fail"
    assert "banned" in session_tip


def test_check_states_new_account_is_warn_not_fail() -> None:
    card = _base_card().model_copy(update={"trust_reasons": ["new account"]})

    age_status, _ = _by_label(_check_states(card))["возраст"]

    assert age_status == "warn"


def test_check_states_spam_unknown_with_error_surfaces_detail() -> None:
    """When the probe failed, the chip tooltip carries the actual exception."""
    card = _base_card().model_copy(
        update={"spam_status": "unknown", "spam_detail": "TimeoutError: timed out"},
    )

    spam_status, spam_tip = _by_label(_check_states(card))["@SpamBot"]

    assert spam_status == "warn"
    assert "TimeoutError" in spam_tip


def test_check_states_spam_unknown_being_checked_mentions_telegram() -> None:
    card = _base_card().model_copy(
        update={"spam_status": "unknown", "spam_detail": "account is being checked"},
    )

    spam_status, spam_tip = _by_label(_check_states(card))["@SpamBot"]

    assert spam_status == "warn"
    assert "Telegram" in spam_tip


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


def _board_from(card: WarmingAccountState) -> WarmingBoardState:
    return WarmingBoardState(
        idle=[card],
        warming=[],
        channels=WarmingChannelList(channels=[]),
        settings=WarmingSettings(gemini_model="m", updated_at="2026-01-01T00:00:00+00:00"),
        channel_count=0,
        active_count=0,
    )


def test_board_signature_reacts_to_last_error() -> None:
    base = _base_card()
    changed = base.model_copy(update={"last_error": "PEER_FLOOD"})
    assert _board_signature(_board_from(base)) != _board_signature(_board_from(changed))


def test_board_signature_reacts_to_flood_wait_until() -> None:
    base = _base_card()
    changed = base.model_copy(update={"flood_wait_until": "2026-06-20T00:00:00+00:00"})
    assert _board_signature(_board_from(base)) != _board_signature(_board_from(changed))


def test_board_signature_reacts_to_quarantine_count() -> None:
    base = _base_card()
    changed = base.model_copy(update={"quarantine_count": 2})
    assert _board_signature(_board_from(base)) != _board_signature(_board_from(changed))


def test_board_signature_reacts_to_trust_reasons() -> None:
    base = _base_card().model_copy(
        update={"trust_score": 80, "trust_band": "good", "trust_reasons": ["geo mismatch"]},
    )
    changed = base.model_copy(update={"trust_reasons": ["geo mismatch", "new account"]})
    assert _board_signature(_board_from(base)) != _board_signature(_board_from(changed))


def test_board_signature_reacts_to_geo_pair() -> None:
    base = _base_card().model_copy(update={"phone_country": "RU", "proxy_country": "RU"})
    changed = base.model_copy(update={"proxy_country": "DE"})
    assert _board_signature(_board_from(base)) != _board_signature(_board_from(changed))


def test_board_signature_reacts_to_spam_detail() -> None:
    base = _base_card().model_copy(update={"spam_status": "limited"})
    changed = base.model_copy(update={"spam_detail": "until 2026-08-12"})
    assert _board_signature(_board_from(base)) != _board_signature(_board_from(changed))


def test_board_signature_stable_when_nothing_changes() -> None:
    card = _base_card().model_copy(
        update={"trust_score": 80, "trust_band": "good", "trust_reasons": ["geo mismatch"]},
    )
    assert _board_signature(_board_from(card)) == _board_signature(_board_from(card))
