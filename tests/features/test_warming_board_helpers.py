"""Tests for the pure helpers behind the warming kanban card.

The render functions themselves are UI-thin and excluded from coverage; we
exercise the data-shaping helpers: the trust-reason translator (UI-edge
localisation), and the board signature digest (which controls when the poll
loop redraws — a missed field means a stale card).
"""

from __future__ import annotations

from features.warming._board import _board_signature, _ru_trust_reason
from schemas.warming import (
    WarmingAccountState,
    WarmingBoardState,
    WarmingChannelList,
    WarmingSettings,
)


def test_ru_trust_reason_known_keys() -> None:
    assert _ru_trust_reason("spam-limited") == "спам-ограничения"
    assert _ru_trust_reason("geo mismatch") == "страна номера ≠ страна прокси"
    assert _ru_trust_reason("geo unknown") == "страна не определена"
    assert _ru_trust_reason("proxy failed") == "прокси не работает"
    assert _ru_trust_reason("new account") == "новый аккаунт"
    assert _ru_trust_reason("recent flood") == "недавний flood-wait"


def test_ru_trust_reason_dynamic_prefixes() -> None:
    assert _ru_trust_reason("status banned") == "сессия: banned"
    assert _ru_trust_reason("status unauthorized") == "сессия: unauthorized"
    assert _ru_trust_reason("quarantined x3") == "карантин ×3"


def test_ru_trust_reason_unknown_falls_back_to_raw() -> None:
    assert _ru_trust_reason("totally unknown reason") == "totally unknown reason"
    assert _ru_trust_reason("") == ""


def _board_from(card: WarmingAccountState) -> WarmingBoardState:
    return WarmingBoardState(
        idle=[card],
        warming=[],
        channels=WarmingChannelList(channels=[]),
        settings=WarmingSettings(gemini_model="m", updated_at="2026-01-01T00:00:00+00:00"),
        channel_count=0,
        active_count=0,
    )


def _base_card() -> WarmingAccountState:
    return WarmingAccountState(account_id="acc-1", label="Acc 1", state="idle", health="idle")


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


def test_board_signature_stable_when_nothing_changes() -> None:
    card = _base_card().model_copy(
        update={"trust_score": 80, "trust_band": "good", "trust_reasons": ["geo mismatch"]},
    )
    assert _board_signature(_board_from(card)) == _board_signature(_board_from(card))
