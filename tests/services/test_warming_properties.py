"""Property-based tests for the pure warming logic (channels + pacing).

Hypothesis drives these so the invariants hold across a wide input space, not
just the hand-picked examples in ``test_warming.py``. Under CI they run with the
strict (200) / extended (2000) example budgets from ``conftest.py``.
"""

from __future__ import annotations

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from services.warming import _human_delay, compute_intensity
from services.warming.channels import _normalize_channel

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
    """Any age yields a valid intensity: 1 <= min <= max and reaction in [0, 1]."""
    intensity = compute_intensity(age)
    assert intensity.channels_max >= 1
    assert intensity.channels_min <= intensity.channels_max
    assert 0.0 <= intensity.reaction_probability <= 1.0
    assert isinstance(intensity.dm_allowed, bool)


@given(
    age_a=st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False),
    age_b=st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False),
)
def test_compute_intensity_monotonic_in_age(age_a: float, age_b: float) -> None:
    """Older accounts never get a lower channel ceiling or reaction rate."""
    younger, older = sorted((age_a, age_b))
    low = compute_intensity(younger)
    high = compute_intensity(older)
    assert low.channels_max <= high.channels_max
    assert low.reaction_probability <= high.reaction_probability + 1e-9


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
