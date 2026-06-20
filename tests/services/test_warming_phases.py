"""Tests for the 5-phase warming lifecycle in services.warming.pacing.

The pure helpers — ``_phase_from_age``, ``_phase_cap_by_trust``,
``effective_phase``, ``_phase_progress`` — drive the per-account daily cap
and the phase chip on the kanban card. They have to:

- Honour the day boundaries (2 / 7 / 14 / 29) and the < 72 h hard floor.
- Treat trust as a ceiling, not a floor: a 60-day-old "critical" account
  must come out as ``settling``, not as the higher of (age, trust).
- Round progress and ``days_to_next_phase`` to safe whole values.
"""

from __future__ import annotations

import pytest

from services.warming.pacing import (
    _PHASE_DAILY_CAP,
    _phase_cap_by_trust,
    _phase_from_age,
    _phase_progress,
    compute_intensity,
    effective_phase,
)

# --- _phase_from_age ---------------------------------------------------------


@pytest.mark.parametrize(
    ("hours", "expected"),
    [
        (0.0, "intro"),
        (24.0, "intro"),  # day 1 — still < 72 h floor
        (71.9, "intro"),  # just below the 72-h floor
        (72.0, "settling"),  # crosses the floor exactly at 3 days
        (7 * 24.0, "settling"),  # day 7 — still settling
        (7 * 24.0 + 1, "warming"),  # day 7+ → warming
        (14 * 24.0, "warming"),  # day 14 boundary inclusive
        (14 * 24.0 + 1, "active"),
        (29 * 24.0, "active"),  # day 29 inclusive
        (29 * 24.0 + 1, "warmed"),  # day 30 onward
        (365 * 24.0, "warmed"),  # year-old account
    ],
)
def test_phase_from_age_boundaries(hours: float, expected: str) -> None:
    assert _phase_from_age(hours) == expected


# --- _phase_cap_by_trust -----------------------------------------------------


@pytest.mark.parametrize(
    ("band", "expected"),
    [
        ("excellent", "warmed"),
        ("good", "warmed"),
        ("watch", "active"),
        ("at_risk", "warming"),
        ("critical", "settling"),
        (None, "warmed"),
        ("unknown-band", "warmed"),  # forward-compatible fallback
    ],
)
def test_phase_cap_by_trust(band: str | None, expected: str) -> None:
    assert _phase_cap_by_trust(band) == expected


# --- effective_phase = min(age, trust ceiling) -------------------------------


def test_effective_phase_age_wins_when_lower() -> None:
    # A 5-day-old account with excellent trust still sits in settling.
    assert effective_phase(5 * 24.0, "excellent") == "settling"


def test_effective_phase_trust_ceiling_caps_old_account() -> None:
    # A year-old account with critical trust must drop to settling.
    assert effective_phase(365 * 24.0, "critical") == "settling"
    # ...and to warming for at_risk.
    assert effective_phase(365 * 24.0, "at_risk") == "warming"


def test_effective_phase_intro_never_promoted_by_high_trust() -> None:
    # Sub-72-h account: trust=excellent can't pull it above intro.
    assert effective_phase(12.0, "excellent") == "intro"


# --- _phase_progress ---------------------------------------------------------


def test_phase_progress_start_of_phase_is_zero() -> None:
    # 3 days = start of settling — progress should be 0.0.
    progress, days_to_next = _phase_progress("settling", 3 * 24.0)
    assert progress == pytest.approx(0.0)
    assert days_to_next == 5  # settling boundary is day 7 → 7+1-3 = 5 days


def test_phase_progress_end_of_phase_approaches_one() -> None:
    # 14 days = last day of warming. Span is days 8..14 = 7 days.
    # (14 - 8) / 7 ≈ 0.857.
    progress, days_to_next = _phase_progress("warming", 14 * 24.0)
    assert progress == pytest.approx(0.857, abs=0.01)
    assert days_to_next == 1


def test_phase_progress_terminal_phase_is_none() -> None:
    progress, days_to_next = _phase_progress("warmed", 365 * 24.0)
    assert progress is None
    assert days_to_next is None


# --- compute_intensity returns the phase + daily cap -------------------------


def test_compute_intensity_assigns_correct_daily_cap_by_phase() -> None:
    # 60-day-old account with clean trust → warmed → cap from _PHASE_DAILY_CAP.
    intensity = compute_intensity(60 * 24.0, trust_band="excellent")
    assert intensity.phase == "warmed"
    assert intensity.daily_cap == _PHASE_DAILY_CAP["warmed"]


def test_compute_intensity_trust_gate_lowers_cap() -> None:
    # Same 60-day-old account but critical trust → capped at settling.
    intensity = compute_intensity(60 * 24.0, trust_band="critical")
    assert intensity.phase == "settling"
    assert intensity.daily_cap == _PHASE_DAILY_CAP["settling"]


def test_compute_intensity_intro_for_fresh_account() -> None:
    intensity = compute_intensity(1.0, trust_band="excellent")
    assert intensity.phase == "intro"
    assert intensity.daily_cap == _PHASE_DAILY_CAP["intro"]


def test_compute_intensity_terminal_phase_has_no_next() -> None:
    intensity = compute_intensity(365 * 24.0, trust_band="excellent")
    assert intensity.phase == "warmed"
    assert intensity.progress_to_next is None
    assert intensity.days_to_next_phase is None


def test_compute_intensity_progress_present_for_non_terminal_phase() -> None:
    intensity = compute_intensity(10 * 24.0, trust_band="excellent")
    assert intensity.phase == "warming"
    assert intensity.progress_to_next is not None
    assert intensity.days_to_next_phase is not None


def test_compute_intensity_trust_capped_phase_hides_progress() -> None:
    # A year-old account pinned to ``settling`` by critical trust would otherwise
    # show 100% progress / "0 days to next" forever (#98). Hide the milestone.
    intensity = compute_intensity(365 * 24.0, trust_band="critical")
    assert intensity.phase == "settling"
    assert intensity.progress_to_next is None
    assert intensity.days_to_next_phase is None


def test_compute_intensity_hides_progress_at_trust_ceiling_boundary() -> None:
    # A "watch" account aged into its ceiling phase ("active") must not show a
    # countdown to "warmed" it can't reach while trust stays at watch.
    intensity = compute_intensity(20 * 24.0, trust_band="watch")
    assert intensity.phase == "active"
    assert intensity.progress_to_next is None
    assert intensity.days_to_next_phase is None
