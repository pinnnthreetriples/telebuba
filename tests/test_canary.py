"""Baseline canary — proves pytest runs at all under our strict config."""

from __future__ import annotations

import core
import schemas


def test_baseline_passes() -> None:
    assert 1 + 1 == 2
    assert core is not None
    assert schemas is not None
