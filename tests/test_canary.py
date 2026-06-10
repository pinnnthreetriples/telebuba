"""Baseline canary — proves pytest runs at all under our strict config."""

from __future__ import annotations


def test_baseline_passes() -> None:
    assert 1 + 1 == 2
