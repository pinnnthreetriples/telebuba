"""Property-based tests for the pure warming logic (channels + pacing).

Hypothesis drives these so the invariants hold across a wide input space, not
just the hand-picked examples in ``test_warming.py``. Under CI they run with the
strict (200) / extended (2000) example budgets from ``conftest.py``.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from core.config import settings
from services.warming import _human_delay, compute_intensity
from services.warming.channels import _normalize_channel
from services.warming.pacing import _PHASE_ORDER, _phase_from_age, _shift_to_active_hours

# --------------------------------------------------------------------------- #
# channels._normalize_channel
# --------------------------------------------------------------------------- #

_PREFIXES = ("https://t.me/", "http://t.me/", "t.me/", "telegram.me/")


@pytest.mark.parametrize(
    "raw",
    [
        "https://t.me/testchannel",
        "t.me/testchannel",
        "@testchannel",
        "t.me/+AbC_12345",
        "t.me/joinchat/AbC_12345",
    ],
)
def test_normalize_channel_output_is_clean(raw: str) -> None:
    """A non-None result is non-empty and carries no ``@`` / ``t.me`` prefix."""
    result = _normalize_channel(raw)
    assert result is not None
    assert "https://" not in result
    assert "t.me/" not in result
    assert "@" not in result
    assert not result.startswith("/")
    assert not result.endswith("/")
    if "single" in raw:
        assert "?" not in result


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://t.me/mychannel/123", "mychannel"),
        ("t.me/mychannel/123?single", "mychannel"),
        ("t.me/+AbC_12345", "+AbC_12345"),
        ("t.me/joinchat/AbC_12345", "+AbC_12345"),
        ("t.me/+AbC_12345?x=1", "+AbC_12345"),
        ("t.me/joinchat/AbC_12345?x=1", "+AbC_12345"),
    ],
)
def test_normalize_channel_exact_matches(raw: str, expected: str) -> None:
    assert _normalize_channel(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "mychannel/123",
        "t.me/c/1234567/1",
        "c/12345",
        "t.me/c/invalid",
    ],
)
def test_normalize_channel_rejects_invalid(raw: str) -> None:
    assert _normalize_channel(raw) is None


@given(raw=st.text())
def test_normalize_channel_property_is_clean(raw: str) -> None:
    result = _normalize_channel(raw)
    if result is not None:
        assert result
        assert not result.startswith("@")
        lowered = result.lower()
        assert not any(lowered.startswith(prefix) for prefix in _PREFIXES)


@given(base=st.from_regex(r"[A-Za-z0-9_]{3,20}", fullmatch=True))
def test_normalize_channel_prefix_equivalence(base: str) -> None:
    """``@x`` / ``t.me/x`` / ``https://t.me/x`` all normalise to the same token."""
    canonical = _normalize_channel(base)
    assume(canonical is not None)
    assert _normalize_channel("@" + base) == canonical
    assert _normalize_channel("t.me/" + base) == canonical
    assert _normalize_channel("https://t.me/" + base) == canonical


@given(base=st.from_regex(r"[A-Za-z0-9_]{3,20}", fullmatch=True))
def test_normalize_channel_idempotent(base: str) -> None:
    """Normalising a username-form token again yields the same token."""
    canonical = _normalize_channel(base)
    assume(canonical is not None)
    assert canonical is not None  # narrow for the type checker; assume guarantees it
    assert _normalize_channel(canonical) == canonical


# --------------------------------------------------------------------------- #
# pacing.compute_intensity
# --------------------------------------------------------------------------- #


@given(age=st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False))
def test_compute_intensity_invariants(age: float) -> None:
    """A permissive trust band leaves phase + daily cap purely age-driven.

    The returned phase equals the pure age-phase and the cap is that phase's
    positive budget. (The old channels_max / reaction_probability assertions were
    tautological once the age-ramp retired — those levers are age-independent
    config now, reducing to ``X <= X``. The real age-dependent output is phase + cap.)
    """
    intensity = compute_intensity(age, trust_band="excellent")
    assert intensity.phase == _phase_from_age(age)
    assert intensity.daily_cap == settings.warming.phase_daily_cap[intensity.phase]
    assert intensity.daily_cap >= 1


@given(
    age_a=st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False),
    age_b=st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False),
)
def test_compute_intensity_monotonic_in_age(age_a: float, age_b: float) -> None:
    """An older account is never in an earlier phase nor gets a smaller daily cap.

    Trust is fixed at a band that permits every phase, so this is the genuine
    age-ramp invariant (channels_max / reaction_probability are age-independent
    config). It fails loudly if the phase→cap mapping is ever inverted, since a
    young ``intro`` account would then out-cap an old ``warmed`` one.
    """
    younger, older = sorted((age_a, age_b))
    low = compute_intensity(younger, trust_band="excellent")
    high = compute_intensity(older, trust_band="excellent")
    assert _PHASE_ORDER.index(low.phase) <= _PHASE_ORDER.index(high.phase)
    assert low.daily_cap <= high.daily_cap


# --------------------------------------------------------------------------- #
# _cycle._human_delay
# --------------------------------------------------------------------------- #


@given(
    a=st.floats(min_value=0.0, max_value=3600.0, allow_nan=False, allow_infinity=False),
    b=st.floats(min_value=0.0, max_value=3600.0, allow_nan=False, allow_infinity=False),
)
def test_human_delay_stays_within_range(a: float, b: float) -> None:
    """The log-normal draw is always clamped into ``[min, max]``."""
    low, high = sorted((a, b))
    result = _human_delay(a, b)
    assert low <= result <= high


# --------------------------------------------------------------------------- #
# pacing._shift_to_active_hours
# --------------------------------------------------------------------------- #

_IANA_TZS = [
    "UTC",
    "America/New_York",
    "Europe/London",
    "Europe/Istanbul",
    "Asia/Tokyo",
    "Asia/Kolkata",
    "Australia/Sydney",
    "America/Sao_Paulo",
    "Pacific/Auckland",
]

# Module-level so Hypothesis doesn't trip the function-scoped-fixture health check;
# the invariant holds for every rng output, so determinism is not required.
_SHIFT_RNG = random.Random(0)  # noqa: S311 - non-crypto jitter in a test


@given(
    # Hypothesis requires naive bounds for st.datetimes; we attach UTC below.
    naive=st.datetimes(min_value=datetime(2020, 1, 1), max_value=datetime(2035, 1, 1)),  # noqa: DTZ001
    tz_name=st.sampled_from(_IANA_TZS),
)
def test_shift_result_lands_in_active_window(naive: datetime, tz_name: str) -> None:
    """A shifted next-run never precedes the candidate and lands inside the window.

    Shifting is enabled by the config default (08:00–23:00), so any candidate is
    either already in-window (returned as-is) or snapped forward into
    ``[start, start + spread)`` — in both cases the account-local hour is in-window.
    """
    candidate = naive.replace(tzinfo=UTC)
    result = _shift_to_active_hours(candidate, tz_name, _SHIFT_RNG)
    warm = settings.warming
    assert result >= candidate  # shifting only ever defers, never brings a run forward
    local_hour = result.astimezone(ZoneInfo(tz_name)).hour
    assert warm.active_hours_start <= local_hour < warm.active_hours_end
