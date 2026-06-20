"""Stability tests for the warming-card signature.

The board polls every 4 seconds and uses ``_card_signature`` to decide
whether to refresh each card's DOM subtree. If the signature drifts on
inputs the operator hasn't touched, the per-card subtree rebuilds on
every poll and the card visibly flickers — exactly the bug the warming
audit set out to fix.

These tests pin the contract:

- ``_card_signature`` is stable across repeated calls on the same
  ``WarmingAccountState``.
- The per-account age drift in ``_phase_progress`` (which recomputes
  every poll from ``datetime.now()``) does not flip the signature once
  the quantisation kicks in.
- A mutation of any field the operator actually sees DOES flip it.
"""

from __future__ import annotations

import pytest

from features.warming._board import _card_signature, _structural_signature
from schemas.warming import WarmingAccountState, WarmingBoardState
from services.warming.pacing import _phase_progress


def _card(**overrides: object) -> WarmingAccountState:
    base: dict[str, object] = {
        "account_id": "acc-1",
        "label": "Acc 1",
        "state": "idle",
        "health": "idle",
    }
    base.update(overrides)
    return WarmingAccountState.model_validate(base)


def test_card_signature_idempotent_on_repeated_calls() -> None:
    card = _card(trust_score=80, trust_band="good", trust_reasons=["geo mismatch"])
    sigs = {_card_signature(card) for _ in range(1000)}
    assert len(sigs) == 1, "signature must be deterministic across repeated calls"


def test_phase_progress_quantised_against_seconds_of_drift() -> None:
    """1 second of age drift must not change the quantised progress field.

    The board polls every 4 seconds. If sub-tick age drift were to bump
    ``progress_to_next`` even by 0.0001, the card signature would mismatch
    on every poll and the DOM would rebuild — that was the visible flicker
    pre-fix. Rounding inside ``_phase_progress`` to 1 % is what makes the
    signature actually stable on a quiet account.
    """
    progress_a, _ = _phase_progress("settling", 100.0)
    progress_b, _ = _phase_progress("settling", 100.01)
    assert progress_a == progress_b


def test_phase_progress_quantised_across_4_second_poll_drift() -> None:
    """Four real poll ticks should produce the same progress value."""
    base = 100.0
    progresses = {_phase_progress("settling", base + i * (4 / 3600))[0] for i in range(4)}
    assert len(progresses) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("state", "active"),
        ("cycles_completed", 42),
        ("last_event", "cycle:ok"),
        ("trust_score", 75),
        ("spam_status", "limited"),
        ("daily_actions", 9),
        ("dm_allowed", True),
        ("quarantine_count", 1),
        ("phone_country", "DE"),
        ("phase", "settling"),
        ("warming_days", 3),
    ],
)
def test_card_signature_reacts_to_real_mutations(field: str, value: object) -> None:
    """Every operator-visible field must still flip the signature."""
    base = _card()
    changed = base.model_copy(update={field: value})
    assert _card_signature(base) != _card_signature(changed), (
        f"signature did not react to mutation of {field}"
    )


def _board(**summary_overrides: object) -> WarmingBoardState:
    return WarmingBoardState.model_validate(
        {
            "channels": {"channels": []},
            "settings": {"gemini_model": "m", "updated_at": "t"},
            "channel_count": 0,
            "active_count": 0,
            "summary": dict(summary_overrides),
        },
    )


@pytest.mark.parametrize("field", ["ready", "attention", "trust_watch"])
def test_structural_signature_reacts_to_summary_drift(field: str) -> None:
    """Summary roll-ups must flip the structural signature so header chips refresh (#98)."""
    base = _board()
    changed = _board(**{field: 3})
    assert _structural_signature(base) != _structural_signature(changed)
