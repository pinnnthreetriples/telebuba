"""Exhaustive state and numeric-boundary contracts for warming schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas.warming import (
    WarmingHealth,
    WarmingIntensity,
    WarmingState,
    WarmingStateWrite,
    is_warming,
    warming_health,
)


@pytest.mark.parametrize(
    ("state", "health", "running"),
    [
        ("idle", "idle", False),
        ("active", "ok", True),
        ("sleeping", "warn", True),
        ("flood_wait", "warn", True),
        ("quarantine", "warn", True),
        ("error", "fail", True),
    ],
)
def test_every_warming_state_has_an_explicit_health_and_membership_contract(
    state: WarmingState,
    health: WarmingHealth,
    *,
    running: bool,
) -> None:
    assert warming_health(state) == health
    assert is_warming(state) is running


@pytest.mark.parametrize(
    "overrides",
    [
        {"channels_min": 1, "channels_max": 1, "reaction_probability": 0.0, "daily_cap": 0},
        {"channels_min": 1, "channels_max": 1, "reaction_probability": 1.0, "daily_cap": 1},
        {
            "channels_min": 1,
            "channels_max": 1,
            "reaction_probability": 0.5,
            "progress_to_next": 0.0,
            "days_to_next_phase": 0,
        },
        {
            "channels_min": 1,
            "channels_max": 1,
            "reaction_probability": 0.5,
            "progress_to_next": 1.0,
            "days_to_next_phase": 1,
        },
    ],
)
def test_warming_intensity_accepts_inclusive_numeric_boundaries(
    overrides: dict[str, object],
) -> None:
    model = WarmingIntensity.model_validate({"dm_allowed": False, **overrides})
    assert model.channels_min == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"channels_min": 0},
        {"channels_max": 0},
        {"reaction_probability": -0.001},
        {"reaction_probability": 1.001},
        {"daily_cap": -1},
        {"progress_to_next": -0.001},
        {"progress_to_next": 1.001},
        {"days_to_next_phase": -1},
    ],
)
def test_warming_intensity_rejects_values_just_outside_boundaries(
    overrides: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "channels_min": 1,
        "channels_max": 1,
        "reaction_probability": 0.5,
        "dm_allowed": False,
    }
    values.update(overrides)
    with pytest.raises(ValidationError):
        WarmingIntensity.model_validate(values)


@pytest.mark.parametrize("field", ["cycles_completed", "daily_actions", "quarantine_count"])
def test_warming_state_write_counters_reject_negative_values(field: str) -> None:
    with pytest.raises(ValidationError):
        WarmingStateWrite.model_validate({"account_id": "acc", "state": "active", field: -1})


def test_warming_state_write_preserves_compare_and_swap_controls() -> None:
    write = WarmingStateWrite(
        account_id="acc",
        state="sleeping",
        run_id="new-generation",
        expected_run_id="old-generation",
        increment_cycle=True,
        target_days=1,
        activity_persona="calm",
    )

    assert write.model_dump()["run_id"] == "new-generation"
    assert write.expected_run_id == "old-generation"
    assert write.increment_cycle is True
    assert write.target_days == 1
    assert write.activity_persona == "calm"
