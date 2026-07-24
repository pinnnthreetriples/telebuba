"""Execution-environment contracts for the Nightly mutation reporter."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.tooling import test_mutation_report as base
from tools import mutation_report

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("profile", "max_children", "message"),
    [
        ("strict", 4, "Hypothesis profile"),
        ("mutation", 8, "max_children"),
    ],
)
def test_baseline_integrity_rejects_execution_parameter_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    profile: str,
    max_children: int,
    message: str,
) -> None:
    base._pin_version(monkeypatch)
    project = tmp_path / "pyproject.toml"
    project.write_text(base._project_config(), encoding="utf-8")

    with pytest.raises(mutation_report.ReportError, match=message):
        mutation_report.validate_baseline_integrity(
            base._baseline(),
            project,
            profile,
            max_children,
        )


@pytest.mark.parametrize(
    ("python_hash_seed", "timezone", "message"),
    [
        ("random", "UTC", "hash seed"),
        ("0", "America/New_York", "timezone"),
    ],
)
def test_baseline_integrity_rejects_process_environment_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    python_hash_seed: str,
    timezone: str,
    message: str,
) -> None:
    base._pin_version(monkeypatch)
    project = tmp_path / "pyproject.toml"
    project.write_text(base._project_config(), encoding="utf-8")

    with pytest.raises(mutation_report.ReportError, match=message):
        mutation_report.validate_baseline_integrity(
            base._baseline(),
            project,
            "mutation",
            4,
            python_hash_seed,
            timezone,
        )
