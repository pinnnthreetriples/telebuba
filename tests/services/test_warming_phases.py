"""Tests for the 5-phase warming lifecycle in services.warming.pacing.

The pure helpers — ``_phase_from_age``, ``_phase_cap_by_trust``,
``effective_phase``, ``_phase_progress`` — drive the per-account daily cap
and the phase chip on the kanban card. They have to:

- Honour the day boundaries (1 / 7 / 14 / 29) and the < 24 h hard floor.
- Treat trust as a ceiling, not a floor: a 60-day-old "critical" account
  must come out as ``settling``, not as the higher of (age, trust).
- Round progress and ``days_to_next_phase`` to safe whole values.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from services.warming.pacing import (
    _PHASE_DAILY_CAP,
    _phase_cap_by_trust,
    _phase_from_age,
    _phase_progress,
    compute_intensity,
    effective_phase,
    warming_days_since,
)

# --- _phase_from_age ---------------------------------------------------------


@pytest.mark.parametrize(
    ("hours", "expected"),
    [
        (0.0, "intro"),
        (23.9, "intro"),  # just below the 24-h hard floor
        (24.0, "intro"),  # day 1 — top of intro (intro day-bound is 1)
        (24.1, "settling"),  # just past day 1 → settling
        (72.0, "settling"),  # day 3 — settling
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


def test_phase_progress_just_after_boundary_is_positive() -> None:
    # Just past the day-1 boundary the account enters settling (``_phase_from_age``
    # flips at ``days > bound``). Progress must be >0 there, not clamped to 0 for
    # the whole first day. settling occupies (1, 7], so span = 6.
    progress, days_to_next = _phase_progress("settling", 1.5 * 24.0)
    assert progress is not None
    # (1.5 - 1) / (7 - 1) ≈ 0.083 — small but strictly positive.
    assert progress > 0.0
    assert progress == pytest.approx(0.083, abs=0.01)
    # Still settling until day 7 flips to warming → 7 - int(1.5) = 6 whole days.
    assert days_to_next == 6


def test_phase_progress_is_monotonic_across_phase() -> None:
    # Progress rises monotonically from the phase's lower edge to its bound.
    days = [1.01, 2.0, 4.0, 6.0, 7.0]  # settling: (1, 7]
    values = [_phase_progress("settling", d * 24.0)[0] for d in days]
    assert all(v is not None for v in values)
    assert values == sorted(values)  # non-decreasing
    assert values[-1] == pytest.approx(1.0)  # day 7 = top of settling


def test_phase_progress_days_to_next_matches_flip_day() -> None:
    # ``days_to_next`` must line up with ``_phase_from_age``'s actual flip. warming
    # occupies (7, 14]; on day 8 the flip to active is 6 whole days away (at day 14).
    _progress, days_to_next = _phase_progress("warming", 8 * 24.0)
    assert days_to_next == 6
    # Sanity: the phase is still warming right up to and including day 14, then flips.
    assert _phase_from_age(14 * 24.0) == "warming"
    assert _phase_from_age(14 * 24.0 + 1) == "active"


def test_phase_progress_end_of_phase_reaches_one() -> None:
    # 14 days = top of warming (its bound). Span is days (7, 14] = 7 days.
    # (14 - 7) / 7 = 1.0.
    progress, days_to_next = _phase_progress("warming", 14 * 24.0)
    assert progress == pytest.approx(1.0)
    assert days_to_next == 0  # the very next day flips to active


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


# --- warming_days_since caps at stopped_at once stopped -----------------------


def test_warming_days_still_growing_while_warming() -> None:
    # A live warming account keeps counting wall-clock (no cap applied).
    started = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    days = warming_days_since(started, datetime.now(UTC), stopped_at=None, state="active")
    assert days == 5


def test_warming_days_frozen_after_stop_regardless_of_now() -> None:
    # A stopped/promoted account's warming_days is frozen at the stop point,
    # not the live clock — otherwise the warmed card's "X/Y days" climbs past Y.
    start = datetime(2026, 1, 1, tzinfo=UTC)
    stopped = start + timedelta(days=4)
    started_iso, stopped_iso = start.isoformat(), stopped.isoformat()

    # 30 days after the stop, the count is still frozen at 4 (idle = not warming).
    long_after = stopped + timedelta(days=30)
    days = warming_days_since(started_iso, long_after, stopped_at=stopped_iso, state="idle")
    assert days == 4

    # ...and does not change as ``now`` advances further.
    even_later = stopped + timedelta(days=100)
    assert warming_days_since(started_iso, even_later, stopped_at=stopped_iso, state="idle") == 4


def test_warming_days_no_cap_when_state_omitted() -> None:
    # Back-compat: without ``state`` the old wall-clock behaviour is preserved
    # (the board wrapper's legacy call site must keep working).
    started = (datetime.now(UTC) - timedelta(days=6)).isoformat()
    assert warming_days_since(started, datetime.now(UTC)) == 6
