"""Tests for the pure preset-detection helpers in features.warming._config.

The render code is UI-thin and excluded from coverage; the detection helpers
are pure functions that decide which select option the page should land on
when the saved settings are loaded. They have to map every recommended
preset back to its key (so an operator who saved the "29-я неделя" preset
sees that preset selected, not "custom"), and gracefully fall back to
``"custom"`` for any value combination the presets don't cover.
"""

from __future__ import annotations

from features.warming._config import (
    _DAILY_PRESETS,
    _QUIET_PRESETS,
    _detect_daily_preset,
    _detect_quiet_preset,
)


def test_detect_quiet_preset_off_when_disabled() -> None:
    """Disabled toggle → "off" regardless of saved hours."""
    assert _detect_quiet_preset(enabled=False, start=23, end=7) == "off"
    assert _detect_quiet_preset(enabled=False, start=0, end=0) == "off"


def test_detect_quiet_preset_matches_each_preset() -> None:
    """Every recommended preset round-trips through detection."""
    for key, (start, end) in _QUIET_PRESETS.items():
        assert _detect_quiet_preset(enabled=True, start=start, end=end) == key


def test_detect_quiet_preset_falls_back_to_custom() -> None:
    """Hour pair that matches no preset → "custom"."""
    assert _detect_quiet_preset(enabled=True, start=5, end=13) == "custom"
    assert _detect_quiet_preset(enabled=True, start=1, end=2) == "custom"


def test_detect_daily_preset_matches_each_preset() -> None:
    """Every recommended daily-limit value resolves to its preset key."""
    for key, value in _DAILY_PRESETS.items():
        assert _detect_daily_preset(value) == key


def test_detect_daily_preset_zero_is_unlimited() -> None:
    """0 is the "Без лимита" preset, not "custom"."""
    assert _detect_daily_preset(0) == "unlimited"


def test_detect_daily_preset_falls_back_to_custom() -> None:
    """Any value not in the preset table → "custom"."""
    assert _detect_daily_preset(47) == "custom"
    assert _detect_daily_preset(1) == "custom"
    assert _detect_daily_preset(999) == "custom"
