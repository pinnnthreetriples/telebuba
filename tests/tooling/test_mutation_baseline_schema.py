"""Schema contracts for the pinned mutation baseline document."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tests.tooling import test_mutation_report as base
from tools import mutation_report

if TYPE_CHECKING:
    from pathlib import Path

_DUPLICATE = ["services.alpha.x_second__mutmut_1", "services.alpha.x_second__mutmut_1"]
_UNSORTED = ["services.alpha.x_second__mutmut_1", "services.alpha.x_first__mutmut_2"]


def _written(tmp_path: Path, baseline: dict[str, object]) -> Path:
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(baseline), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("baseline_change", "message"),
    [
        ({"mutant_catalog_sha256": "not-a-digest"}, "lowercase SHA-256"),
        ({"hypothesis_profile": ""}, "must be a non-empty string"),
        ({"max_children": 0}, "must be a positive integer"),
        ({"reviewed_timeouts": _DUPLICATE}, "must not contain duplicates"),
        ({"reviewed_survivors": _DUPLICATE}, "must not contain duplicates"),
        ({"reviewed_survivors": _UNSORTED}, "reviewed_survivors must be sorted"),
        ({"reviewed_timeouts": []}, "timeout count must match"),
        ({"reviewed_survivors": []}, "survived count must match"),
    ],
)
def test_load_baseline_rejects_invalid_measurement_metadata(
    tmp_path: Path,
    baseline_change: dict[str, object],
    message: str,
) -> None:
    baseline = base._baseline()
    baseline.update(baseline_change)

    with pytest.raises(mutation_report.ReportError, match=message):
        mutation_report.load_baseline(_written(tmp_path, baseline))


def test_load_baseline_rejects_a_mutant_reviewed_as_both_timeout_and_survivor(
    tmp_path: Path,
) -> None:
    shared = "services.alpha.x_second__mutmut_1"
    baseline = base._baseline(reviewed_survivors=[shared], survived=1)

    with pytest.raises(mutation_report.ReportError, match="claim one mutant twice"):
        mutation_report.load_baseline(_written(tmp_path, baseline))


@pytest.mark.parametrize(
    ("stats", "message"),
    [
        ({"killed": 5, "survived": 0, "timeout": 1, "total": 4}, "exceed total"),
        ({"killed": 2, "survived": 2, "timeout": 1, "total": 4}, "must equal total"),
        ({"killed": 1, "survived": 1, "timeout": 0, "total": 10}, "must equal total"),
    ],
)
def test_load_baseline_rejects_impossible_stats(
    tmp_path: Path,
    stats: dict[str, int],
    message: str,
) -> None:
    baseline = base._baseline()
    baseline["stats"] = stats

    with pytest.raises(mutation_report.ReportError, match=message):
        mutation_report.load_baseline(_written(tmp_path, baseline))
