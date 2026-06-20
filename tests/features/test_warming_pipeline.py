"""Unit tests for the pure step-resolution logic behind the warming pipeline.

The render functions in ``_pipeline`` are UI-thin and excluded from coverage;
these cover the deterministic resolvers that decide *which* step is live and
*how* each node / connector should look. The index maths (the next-step bump,
the clamp at sleep, the error-step fallback) is the bug-prone part.
"""

from __future__ import annotations

import pytest

from features.warming._pipeline import (
    _SLEEP_STEP_INDEX,
    _active_step,
    _connector_kind,
    _next_active_index,
    _step_kind,
)
from schemas.warming import WarmingAccountState


def _card(state: str, last_action: str | None = None) -> WarmingAccountState:
    # model_copy(update=...) takes a dict[str, Any], so state/last_action skip
    # the field Literal check — same pattern as test_warming_board_helpers.
    base = WarmingAccountState(account_id="acc-1", label="Acc 1", state="idle", health="idle")
    return base.model_copy(update={"state": state, "last_action": last_action})


@pytest.mark.parametrize(
    ("last_action", "expected"),
    [
        ("set_online", 1),
        ("join", 2),
        ("read_or_react", 4),
        ("send_dm", _SLEEP_STEP_INDEX),  # clamped at the sleep step, not 5+1
        (None, 1),
        ("unknown", 1),  # unknown action falls back to online -> next is 1
    ],
)
def test_next_active_index(last_action: str | None, expected: int) -> None:
    assert _next_active_index(_card("active", last_action)) == expected


@pytest.mark.parametrize(
    ("state", "last_action", "expected"),
    [
        ("quarantine", None, (None, "quar")),
        ("flood_wait", None, (_SLEEP_STEP_INDEX, "flood")),
        ("sleeping", None, (_SLEEP_STEP_INDEX, "sleep")),
        ("error", "send_dm", (4, "error")),
        ("error", None, (0, "error")),  # unknown action pins the error to online
        ("active", "join", (2, "active")),
        ("idle", None, (None, "active")),  # caller gates idle; defensive default
    ],
)
def test_active_step(
    state: str,
    last_action: str | None,
    expected: tuple[int | None, str],
) -> None:
    assert _active_step(_card(state, last_action)) == expected


@pytest.mark.parametrize(
    ("idx", "active_idx", "kind", "expected"),
    [
        (2, None, "quar", "quar"),  # quarantine dims every node
        (0, None, "active", "pending"),  # nothing live yet
        (3, 1, "active", "pending"),  # ahead of the live step
        (0, 3, "active", "done"),  # behind the live step
        (3, 3, "active", "active"),  # the live step itself
        (_SLEEP_STEP_INDEX, _SLEEP_STEP_INDEX, "flood", "flood"),
        (2, 2, "error", "error"),
        (_SLEEP_STEP_INDEX, _SLEEP_STEP_INDEX, "sleep", "active"),  # sleep reuses the active slot
    ],
)
def test_step_kind(idx: int, active_idx: int | None, kind: str, expected: str) -> None:
    assert _step_kind(idx, active_idx, kind) == expected


@pytest.mark.parametrize(
    ("left_idx", "active_idx", "kind", "expected"),
    [
        (0, None, "active", "pending"),  # no live step
        (2, 3, "active", "active"),  # connector flowing into the live step
        (0, 3, "active", "done"),  # fully behind the live step
        (2, 3, "sleep", "done"),  # into-active position but the rail is resting
        (3, 3, "active", "pending"),  # at the live step
        (4, 3, "active", "pending"),  # past the live step
    ],
)
def test_connector_kind(
    left_idx: int,
    active_idx: int | None,
    kind: str,
    expected: str,
) -> None:
    assert _connector_kind(left_idx, active_idx, kind) == expected
