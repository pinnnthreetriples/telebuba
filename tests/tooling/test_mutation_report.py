"""Contract tests for the Nightly mutation reporter and regression gate."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tools import mutation_report

if TYPE_CHECKING:
    from pathlib import Path


def _stats(**overrides: int) -> dict[str, int]:
    values = {
        "killed": 2,
        "survived": 1,
        "total": 4,
        "no_tests": 0,
        "skipped": 0,
        "suspicious": 0,
        "timeout": 1,
        "check_was_interrupted_by_user": 0,
        "segfault": 0,
    }
    values.update(overrides)
    return values


def _baseline(**overrides: int) -> dict[str, object]:
    stats = {"killed": 1, "survived": 1, "timeout": 0, "total": 2}
    stats.update(overrides)
    return {
        "mutmut_version": "3.6.0",
        "mutmut_config": _mutmut_config(),
        "stats": stats,
    }


def _mutmut_config() -> dict[str, object]:
    return {
        "source_paths": ["services", "schemas"],
        "paths_to_exclude": None,
        "mutate_only_covered_lines": True,
        "pytest_add_cli_args_test_selection": ["tests"],
        "pytest_add_cli_args": ["-o", "addopts="],
        "also_copy": ["tests", "tools"],
    }


def _results() -> str:
    return (
        "    services.alpha.x_first__mutmut_1: killed\n"
        "    services.alpha.x_first__mutmut_2: survived\n"
        "    services.alpha.x_second__mutmut_1: timeout\n"
        "    schemas.beta.xǁModelǁvalidate__mutmut_1: killed"
    )


def _project_config(**overrides: object) -> str:
    config = _mutmut_config()
    config.update(overrides)
    lines = ["[tool.mutmut]"]
    for key, value in config.items():
        if value is None:
            continue
        lines.append(f"{key} = {json.dumps(value).lower()}")
    return "\n".join(lines) + "\n"


def _write_inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    stats = tmp_path / "stats.json"
    results = tmp_path / "results.txt"
    baseline = tmp_path / "baseline.json"
    project = tmp_path / "pyproject.toml"
    stats.write_text(json.dumps(_stats()), encoding="utf-8")
    results.write_text(_results(), encoding="utf-8")
    baseline.write_text(json.dumps(_baseline()), encoding="utf-8")
    project.write_text(_project_config(), encoding="utf-8")
    return stats, results, baseline, project


def _pin_version(monkeypatch: pytest.MonkeyPatch, version: str = "3.6.0") -> None:
    monkeypatch.setattr(mutation_report.importlib.metadata, "version", lambda _name: version)


def test_load_results_preserves_status_and_derives_hotspot_names(tmp_path: Path) -> None:
    path = tmp_path / "results.txt"
    path.write_text(_results(), encoding="utf-8")

    results = mutation_report.load_results(path)

    assert [(item.module, item.function, item.status) for item in results] == [
        ("services.alpha", "first", "killed"),
        ("services.alpha", "first", "survived"),
        ("services.alpha", "second", "timeout"),
        ("schemas.beta", "Model.validate", "killed"),
    ]


@pytest.mark.parametrize(
    "line",
    [
        "services.alpha.x_first__mutmut_1: survived",  # indentation is part of 3.6 output
        "    services.alpha.x_first__mutmut_1 survived",
        "    services.alpha.x_first__mutmut_1: future-status",
        "mutmut summary: 1 survivor",
    ],
)
def test_load_results_rejects_unknown_format(tmp_path: Path, line: str) -> None:
    path = tmp_path / "results.txt"
    path.write_text(line, encoding="utf-8")

    with pytest.raises(mutation_report.ReportError):
        mutation_report.load_results(path)


def test_load_results_rejects_duplicate_mutant(tmp_path: Path) -> None:
    path = tmp_path / "results.txt"
    line = "    services.alpha.x_first__mutmut_1: survived\n"
    path.write_text(line * 2, encoding="utf-8")

    with pytest.raises(mutation_report.ReportError, match="duplicate mutant"):
        mutation_report.load_results(path)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"killed": True}, "non-negative integer"),
        ({"timeout": -1}, "non-negative integer"),
        ({"total": 0, "killed": 0, "survived": 0, "timeout": 0}, "positive"),
    ],
)
def test_load_stats_rejects_invalid_or_incomplete_export(
    tmp_path: Path,
    change: dict[str, int],
    message: str,
) -> None:
    path = tmp_path / "stats.json"
    path.write_text(json.dumps(_stats(**change)), encoding="utf-8")

    with pytest.raises(mutation_report.ReportError, match=message):
        mutation_report.load_stats(path)


def test_build_report_has_exact_score_delta_hotspots_and_timeouts(tmp_path: Path) -> None:
    results_path = tmp_path / "results.txt"
    results_path.write_text(_results(), encoding="utf-8")

    report = mutation_report.build_report(
        _stats(),
        mutation_report.load_results(results_path),
        _baseline(),
    )

    assert report["score_percent"] == "50.0000"
    assert report["delta_percentage_points"] == "0.0000"
    assert report["meets_baseline"] is True
    assert report["module_hotspots"] == [{"module": "services.alpha", "count": 2}]
    assert report["function_hotspots"] == [
        {"function": "services.alpha::first", "count": 1},
        {"function": "services.alpha::second", "count": 1},
    ]
    assert report["timeouts"] == ["services.alpha.x_second__mutmut_1"]


def test_gate_uses_exact_fraction_not_rounded_display(tmp_path: Path) -> None:
    results_path = tmp_path / "results.txt"
    results_path.write_text(
        "    services.a.x_f__mutmut_1: killed\n"
        "    services.a.x_f__mutmut_2: survived\n"
        "    services.a.x_f__mutmut_3: survived",
        encoding="utf-8",
    )
    report = mutation_report.build_report(
        _stats(killed=1, survived=2, timeout=0, total=3),
        mutation_report.load_results(results_path),
        _baseline(killed=2, survived=4, total=6),
    )
    assert report["score_percent"] == "33.3333"
    assert report["meets_baseline"] is True


def test_build_report_rejects_results_stats_disagreement(tmp_path: Path) -> None:
    path = tmp_path / "results.txt"
    path.write_text(_results(), encoding="utf-8")

    with pytest.raises(mutation_report.ReportError, match="mismatch for killed"):
        mutation_report.build_report(
            _stats(killed=1, survived=2),
            mutation_report.load_results(path),
            _baseline(),
        )


def test_build_report_accepts_officially_omitted_type_check_status(tmp_path: Path) -> None:
    path = tmp_path / "results.txt"
    path.write_text(
        "    services.a.x_f__mutmut_1: killed\n    services.a.x_f__mutmut_2: caught by type check",
        encoding="utf-8",
    )

    report = mutation_report.build_report(
        _stats(killed=1, survived=0, timeout=0, total=2),
        mutation_report.load_results(path),
        _baseline(),
    )

    assert report["score_percent"] == "50.0000"


@pytest.mark.parametrize("status", ["not checked", "check was interrupted by user"])
def test_build_report_rejects_incomplete_run(tmp_path: Path, status: str) -> None:
    path = tmp_path / "results.txt"
    path.write_text(f"    services.a.x_f__mutmut_1: {status}", encoding="utf-8")
    stats = _stats(killed=0, survived=0, timeout=0, total=1)
    if status == "check was interrupted by user":
        stats["check_was_interrupted_by_user"] = 1

    with pytest.raises(mutation_report.ReportError, match="incomplete"):
        mutation_report.build_report(stats, mutation_report.load_results(path), _baseline())


def test_baseline_integrity_rejects_scope_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pin_version(monkeypatch)
    project = tmp_path / "pyproject.toml"
    project.write_text(_project_config(source_paths=["services"]), encoding="utf-8")

    with pytest.raises(mutation_report.ReportError, match="does not match project config"):
        mutation_report.validate_baseline_integrity(_baseline(), project)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("mutate_only_covered_lines", False),
        ("paths_to_exclude", ["services/generated"]),
        ("pytest_add_cli_args_test_selection", ["tests/services"]),
    ],
)
def test_baseline_integrity_rejects_measurement_config_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    value: object,
) -> None:
    _pin_version(monkeypatch)
    project = tmp_path / "pyproject.toml"
    project.write_text(_project_config(**{key: value}), encoding="utf-8")

    with pytest.raises(mutation_report.ReportError, match="does not match project config"):
        mutation_report.validate_baseline_integrity(_baseline(), project)


def test_baseline_integrity_rejects_version_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pin_version(monkeypatch, "3.7.0")
    project = tmp_path / "pyproject.toml"
    project.write_text(_project_config(), encoding="utf-8")

    with pytest.raises(mutation_report.ReportError, match="does not match installed"):
        mutation_report.validate_baseline_integrity(_baseline(), project)


def test_report_cli_writes_json_and_readable_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pin_version(monkeypatch)
    stats, results, baseline, project = _write_inputs(tmp_path)
    output = tmp_path / "report" / "report.json"
    summary = tmp_path / "report" / "summary.md"

    exit_code = mutation_report.main(
        [
            "report",
            "--stats",
            str(stats),
            "--results",
            str(results),
            "--baseline",
            str(baseline),
            "--project",
            str(project),
            "--output",
            str(output),
            "--summary",
            str(summary),
        ],
    )

    assert exit_code == 0
    assert json.loads(output.read_text())["timeouts"] == [
        "services.alpha.x_second__mutmut_1",
    ]
    rendered = summary.read_text()
    assert "Module hotspots" in rendered
    assert "Timeout mutants" in rendered


def test_gate_cli_fails_only_on_aggregate_score_regression(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _pin_version(monkeypatch)
    stats, results, baseline, project = _write_inputs(tmp_path)
    baseline.write_text(json.dumps(_baseline(killed=3, survived=1, total=4)), encoding="utf-8")

    exit_code = mutation_report.main(
        [
            "gate",
            "--stats",
            str(stats),
            "--results",
            str(results),
            "--baseline",
            str(baseline),
            "--project",
            str(project),
        ],
    )

    assert exit_code == 1
    assert "mutation score regression" in capsys.readouterr().err
