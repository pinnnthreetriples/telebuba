"""Visit cadence (item 4.3): the persona sets the visits, the day's cap is split between them."""

from __future__ import annotations

import random
from typing import get_args

import pytest

from core.config import settings
from schemas import _warming_visits
from schemas.warming import ActivityPersona, WarmingPhase
from services.warming import _phases
from services.warming._cadence import (
    MIN_VISIT_ACTIONS,
    cadence_preview,
    expected_visits,
    persona_visits,
    visit_budget,
)
from services.warming.pacing import persona_next_run_seconds


def test_the_mirrored_literals_match_schemas_warming() -> None:
    assert get_args(_warming_visits.WarmingPhase) == get_args(WarmingPhase)
    assert get_args(_warming_visits.Persona) == get_args(ActivityPersona)


def test_a_cap_of_fifteen_makes_five_or_six_short_visits() -> None:
    rng = random.Random(3)  # noqa: S311 - deterministic test rng
    visits = {persona_visits("normal", 15, rng) for _ in range(200)}
    assert visits == {5, 6}


def test_a_calm_persona_visits_less_than_the_cap_affords() -> None:
    rng = random.Random(3)  # noqa: S311 - deterministic test rng
    visits = {persona_visits("calm", 80, rng) for _ in range(200)}
    low, high = settings.warming.persona_sessions["calm"]
    assert visits <= set(range(low, high + 1))


def test_no_cap_leaves_the_persona_draw() -> None:
    rng = random.Random(3)  # noqa: S311 - deterministic test rng
    low, high = settings.warming.persona_sessions["active"]
    assert low <= persona_visits("active", 0, rng) <= high


def test_the_next_run_gap_follows_the_visits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "active_hours_enabled", True)
    monkeypatch.setattr(settings.warming, "active_hours_start", 8)
    monkeypatch.setattr(settings.warming, "active_hours_end", 23)
    monkeypatch.setattr(settings.warming, "next_run_jitter_fraction", 0.0)
    monkeypatch.setattr(_phases, "persona_visits", lambda *_args: 5)
    gap = persona_next_run_seconds("normal", 15, random.Random(1))  # noqa: S311
    assert gap == pytest.approx(3 * 3600)


@pytest.mark.parametrize(("remaining", "expected"), [(15, 3), (7, 3), (2, 2), (1, 1)])
def test_the_visit_budget_splits_the_remainder(remaining: int, expected: int) -> None:
    assert visit_budget("normal", 15, remaining) == expected


def test_the_visit_budget_never_drops_below_the_floor_nor_exceeds_the_rest() -> None:
    for remaining in range(1, 81):
        budget = visit_budget("active", 80, remaining)
        assert budget is not None
        assert budget <= remaining
        assert budget >= min(MIN_VISIT_ACTIONS, remaining)


def test_no_cap_means_no_visit_budget() -> None:
    assert visit_budget("normal", 0, None) is None
    assert visit_budget("normal", 15, None) is None


def test_expected_visits_is_bounded_by_the_cap() -> None:
    assert expected_visits("active", 15) == pytest.approx(15 / 2.75)
    assert expected_visits("calm", 80) == pytest.approx(6.0)


def test_the_preview_shows_real_numbers_per_phase() -> None:
    rows = {(row.persona, row.phase): row for row in cadence_preview()}
    assert len(rows) == 3 * 5
    intro = rows[("normal", "intro")]
    assert (intro.visits_min, intro.visits_max, intro.actions_per_visit) == (5, 6, 3)
    warmed = rows[("active", "warmed")]
    assert (warmed.visits_min, warmed.visits_max) == (20, 28)
    assert all(row.visits_min <= row.visits_max for row in rows.values())
