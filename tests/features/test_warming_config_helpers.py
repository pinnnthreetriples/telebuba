"""Tests for the pure preset-detection helpers in features.warming._config.

The quiet-hours preset detector is the page's "where do I land on load"
function — every recommended preset must round-trip, and any unrecognised
hour pair must fall back to ``"custom"`` so the page reveals the manual
controls instead of silently dropping the saved value.

(The daily-limit preset selector was removed when the daily cap became
per-account auto-derived from phase + trust — those tests are gone with it.)
"""

from __future__ import annotations

from features.warming._config import _QUIET_PRESETS, _detect_quiet_preset


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
