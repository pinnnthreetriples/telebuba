"""Contract tests for the Nightly mutation reporter and regression gate."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tools import mutation_report, mutation_report_core

SOURCE_ROOT = Path.cwd()
SOURCE_PATHS = ["services", "schemas"]


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


def _result_objects(results_text: str) -> list[mutation_report.Result]:
    parsed: list[mutation_report.Result] = []
    for line in results_text.splitlines():
        name, status = line.strip().split(": ", 1)
        parsed.append(mutation_report.Result(name=name, status=status))
    return parsed


def _baseline(  # noqa: PLR0913
    *,
    results_text: str | None = None,
    reviewed_timeouts: list[str] | None = None,
    python_version: str | None = None,
    mutant_catalog_sha256: str | None = None,
    hypothesis_profile: str = "mutation",
    max_children: int = 4,
    source_root: Path = SOURCE_ROOT,
    **overrides: int,
) -> dict[str, Any]:
    results_text = results_text or _results()
    results = _result_objects(results_text)
    if reviewed_timeouts is None:
        reviewed_timeouts = sorted(item.name for item in results if item.status == "timeout")
    stats = {
        "killed": sum(item.status == "killed" for item in results),
        "survived": sum(item.status == "survived" for item in results),
        "timeout": len(reviewed_timeouts),
        "total": len(results),
    }
    stats.update(overrides)
    return {
        "mutmut_version": "3.6.0",
        "python_version": python_version or mutation_report.platform.python_version(),
        "hypothesis_profile": hypothesis_profile,
        "max_children": max_children,
        "mutant_catalog_sha256": (
            mutant_catalog_sha256
            or mutation_report.mutant_catalog_sha256(results, source_root, SOURCE_PATHS)
        ),
        "reviewed_timeouts": reviewed_timeouts,
        "mutmut_config": _mutmut_config(),
        "stats": stats,
    }


def _mutmut_config() -> dict[str, object]:
    return {
        **mutation_report_core.CONFIG_DEFAULTS,
        "source_paths": ["services", "schemas"],
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
    config = {
        key: value
        for key, value in _mutmut_config().items()
        if value != mutation_report_core.CONFIG_DEFAULTS[key]
    }
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
    for source_path in SOURCE_PATHS:
        directory = tmp_path / source_path
        directory.mkdir()
        (directory / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
    baseline.write_text(json.dumps(_baseline(source_root=tmp_path)), encoding="utf-8")
    project.write_text(_project_config(), encoding="utf-8")
    return stats, results, baseline, project


def _pin_version(monkeypatch: pytest.MonkeyPatch, version: str = "3.6.0") -> None:
    monkeypatch.setattr(mutation_report.importlib.metadata, "version", lambda _name: version)


def _pin_python(monkeypatch: pytest.MonkeyPatch, version: str) -> None:
    monkeypatch.setattr(mutation_report.platform, "python_version", lambda: version)


def _build_report(
    stats: dict[str, int],
    results: list[mutation_report.Result],
    baseline: dict[str, Any],
    repair_results: list[mutation_report.Result] | None = None,
) -> dict[str, Any]:
    return mutation_report.build_report(
        stats,
        results,
        baseline,
        repair_results,
        source_root=SOURCE_ROOT,
    )


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

    report = _build_report(
        _stats(),
        mutation_report.load_results(results_path),
        _baseline(),
    )

    assert report["score_percent"] == "50.0000"
    assert report["delta_percentage_points"] == "0.0000"
    assert report["meets_score_baseline"] is True
    assert report["meets_baseline"] is True
    assert report["catalog_matches_baseline"] is True
    assert report["unexpected_timeouts"] == []
    assert report["resolved_reviewed_timeouts"] == []
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
    results_text = results_path.read_text(encoding="utf-8")
    report = _build_report(
        _stats(killed=1, survived=2, timeout=0, total=3),
        mutation_report.load_results(results_path),
        _baseline(
            results_text=results_text,
            reviewed_timeouts=[],
            killed=2,
            survived=4,
            timeout=0,
            total=6,
        ),
    )
    assert report["score_percent"] == "33.3333"
    assert report["meets_score_baseline"] is True
    assert report["meets_baseline"] is True


def test_catalog_digest_is_independent_of_result_order() -> None:
    results = _result_objects(_results())

    assert mutation_report.mutant_catalog_sha256(results, SOURCE_ROOT, SOURCE_PATHS) == (
        mutation_report.mutant_catalog_sha256(
            list(reversed(results)),
            SOURCE_ROOT,
            SOURCE_PATHS,
        )
    )


def test_catalog_digest_binds_stable_source_paths_and_bytes(tmp_path: Path) -> None:
    results = _result_objects(_results())
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    for root in (first_root, second_root):
        for source_path in SOURCE_PATHS:
            directory = root / source_path
            directory.mkdir(parents=True)
            (directory / "module.py").write_bytes(b"VALUE = 1\n")

    first = mutation_report.mutant_catalog_sha256(results, first_root, SOURCE_PATHS)
    assert first == mutation_report.mutant_catalog_sha256(results, second_root, SOURCE_PATHS)

    (second_root / "services" / "module.py").write_bytes(b"VALUE = 2\n")
    assert first != mutation_report.mutant_catalog_sha256(results, second_root, SOURCE_PATHS)
    (second_root / "services" / "module.py").rename(second_root / "services" / "renamed.py")
    assert first != mutation_report.mutant_catalog_sha256(results, second_root, SOURCE_PATHS)


def test_build_report_flags_catalog_drift_even_when_total_is_unchanged(tmp_path: Path) -> None:
    changed = _results().replace("x_first__mutmut_1", "x_first__mutmut_9")
    path = tmp_path / "results.txt"
    path.write_text(changed, encoding="utf-8")

    report = _build_report(
        _stats(),
        mutation_report.load_results(path),
        _baseline(),
    )

    assert report["catalog_matches_baseline"] is False
    assert report["meets_score_baseline"] is True
    assert report["meets_baseline"] is False


def test_build_report_flags_only_unreviewed_timeout_identities(tmp_path: Path) -> None:
    path = tmp_path / "results.txt"
    path.write_text(_results(), encoding="utf-8")

    report = _build_report(
        _stats(),
        mutation_report.load_results(path),
        _baseline(reviewed_timeouts=[], timeout=0),
    )

    assert report["unexpected_timeouts"] == ["services.alpha.x_second__mutmut_1"]
    assert report["meets_baseline"] is False


def test_disappearing_reviewed_timeout_is_an_allowed_improvement(tmp_path: Path) -> None:
    improved = _results().replace("x_second__mutmut_1: timeout", "x_second__mutmut_1: killed")
    path = tmp_path / "results.txt"
    path.write_text(improved, encoding="utf-8")

    report = _build_report(
        _stats(killed=3, survived=1, timeout=0),
        mutation_report.load_results(path),
        _baseline(),
    )

    assert report["catalog_matches_baseline"] is True
    assert report["unexpected_timeouts"] == []
    assert report["resolved_reviewed_timeouts"] == ["services.alpha.x_second__mutmut_1"]
    assert report["meets_baseline"] is True


def test_build_report_rejects_results_stats_disagreement(tmp_path: Path) -> None:
    path = tmp_path / "results.txt"
    path.write_text(_results(), encoding="utf-8")

    with pytest.raises(mutation_report.ReportError, match="mismatch for killed"):
        _build_report(
            _stats(killed=1, survived=2),
            mutation_report.load_results(path),
            _baseline(),
        )


def test_build_report_rejects_officially_omitted_type_check_status(tmp_path: Path) -> None:
    path = tmp_path / "results.txt"
    path.write_text(
        "    services.a.x_f__mutmut_1: killed\n    services.a.x_f__mutmut_2: caught by type check",
        encoding="utf-8",
    )

    with pytest.raises(mutation_report.ReportError, match="infrastructure-invalid"):
        _build_report(
            _stats(killed=1, survived=0, timeout=0, total=2),
            mutation_report.load_results(path),
            _baseline(),
        )


@pytest.mark.parametrize(
    ("status", "stats_key"),
    [
        ("no tests", "no_tests"),
        ("skipped", "skipped"),
        ("suspicious", "suspicious"),
        ("segfault", "segfault"),
    ],
)
def test_build_report_rejects_infrastructure_invalid_effective_status(
    status: str,
    stats_key: str,
) -> None:
    results_text = f"    services.a.x_f__mutmut_1: {status}"
    stats = _stats(killed=0, survived=0, timeout=0, total=1, **{stats_key: 1})

    with pytest.raises(mutation_report.ReportError, match="infrastructure-invalid"):
        _build_report(
            stats,
            _result_objects(results_text),
            _baseline(results_text=results_text, reviewed_timeouts=[]),
        )


@pytest.mark.parametrize("status", ["not checked", "check was interrupted by user"])
def test_build_report_rejects_incomplete_run(tmp_path: Path, status: str) -> None:
    path = tmp_path / "results.txt"
    path.write_text(f"    services.a.x_f__mutmut_1: {status}", encoding="utf-8")
    stats = _stats(killed=0, survived=0, timeout=0, total=1)
    if status == "check was interrupted by user":
        stats["check_was_interrupted_by_user"] = 1

    with pytest.raises(mutation_report.ReportError, match="incomplete"):
        _build_report(stats, mutation_report.load_results(path), _baseline())


def test_baseline_integrity_rejects_scope_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pin_version(monkeypatch)
    project = tmp_path / "pyproject.toml"
    project.write_text(_project_config(source_paths=["services"]), encoding="utf-8")

    with pytest.raises(mutation_report.ReportError, match="does not match project config"):
        mutation_report.validate_baseline_integrity(_baseline(), project, "mutation", 4)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("mutate_only_covered_lines", False),
        ("only_mutate", ["services/*.py"]),
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
        mutation_report.validate_baseline_integrity(_baseline(), project, "mutation", 4)


def test_baseline_integrity_rejects_version_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pin_version(monkeypatch, "3.7.0")
    project = tmp_path / "pyproject.toml"
    project.write_text(_project_config(), encoding="utf-8")

    with pytest.raises(mutation_report.ReportError, match="does not match installed"):
        mutation_report.validate_baseline_integrity(_baseline(), project, "mutation", 4)


def test_baseline_integrity_rejects_python_version_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pin_version(monkeypatch)
    _pin_python(monkeypatch, "3.13.14")
    project = tmp_path / "pyproject.toml"
    project.write_text(_project_config(), encoding="utf-8")

    with pytest.raises(mutation_report.ReportError, match=r"Python version .* does not match"):
        mutation_report.validate_baseline_integrity(
            _baseline(python_version="3.13.12"),
            project,
            "mutation",
            4,
        )


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
    _pin_version(monkeypatch)
    project = tmp_path / "pyproject.toml"
    project.write_text(_project_config(), encoding="utf-8")

    with pytest.raises(mutation_report.ReportError, match=message):
        mutation_report.validate_baseline_integrity(
            _baseline(),
            project,
            profile,
            max_children,
        )


@pytest.mark.parametrize(
    ("baseline_change", "message"),
    [
        ({"mutant_catalog_sha256": "not-a-digest"}, "lowercase SHA-256"),
        ({"hypothesis_profile": ""}, "must be a non-empty string"),
        ({"max_children": 0}, "must be a positive integer"),
        (
            {
                "reviewed_timeouts": [
                    "services.alpha.x_second__mutmut_1",
                    "services.alpha.x_second__mutmut_1",
                ],
            },
            "must not contain duplicates",
        ),
        ({"reviewed_timeouts": []}, "timeout count must match"),
    ],
)
def test_load_baseline_rejects_invalid_measurement_metadata(
    tmp_path: Path,
    baseline_change: dict[str, object],
    message: str,
) -> None:
    baseline = _baseline()
    baseline.update(baseline_change)
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(baseline), encoding="utf-8")

    with pytest.raises(mutation_report.ReportError, match=message):
        mutation_report.load_baseline(path)


@pytest.mark.parametrize(
    ("stats", "message"),
    [
        ({"killed": 5, "survived": 0, "timeout": 1, "total": 4}, "exceed total"),
        ({"killed": 2, "survived": 2, "timeout": 1, "total": 4}, "exceed total"),
    ],
)
def test_load_baseline_rejects_impossible_stats(
    tmp_path: Path,
    stats: dict[str, int],
    message: str,
) -> None:
    baseline = _baseline()
    baseline["stats"] = stats
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(baseline), encoding="utf-8")

    with pytest.raises(mutation_report.ReportError, match=message):
        mutation_report.load_baseline(path)


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
            "--hypothesis-profile",
            "mutation",
            "--max-children",
            "4",
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
    assert "Mutant catalog: **matches baseline**" in rendered
    assert "Module hotspots" in rendered
    assert "Timeout mutants" in rendered
    assert "Unexpected timeout mutants\n\nNone." in rendered


def test_gate_cli_fails_only_on_aggregate_score_regression(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _pin_version(monkeypatch)
    stats, results, baseline, project = _write_inputs(tmp_path)
    baseline.write_text(
        json.dumps(_baseline(killed=3, survived=0, total=4, source_root=tmp_path)),
        encoding="utf-8",
    )

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
            "--hypothesis-profile",
            "mutation",
            "--max-children",
            "4",
        ],
    )

    assert exit_code == 1
    error = capsys.readouterr().err
    assert "mutation score regression" in error
    assert "catalog drift" not in error
    assert "unexpected timeout" not in error


def test_gate_cli_reports_catalog_drift_distinctly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _pin_version(monkeypatch)
    stats, results, baseline, project = _write_inputs(tmp_path)
    stored = _baseline(mutant_catalog_sha256="0" * 64)
    baseline.write_text(json.dumps(stored), encoding="utf-8")

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
            "--hypothesis-profile",
            "mutation",
            "--max-children",
            "4",
        ],
    )

    assert exit_code == 1
    error = capsys.readouterr().err
    assert "mutant catalog drift" in error
    assert "mutation score regression" not in error
    assert "unexpected timeout" not in error


def test_gate_cli_reports_unexpected_timeout_distinctly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _pin_version(monkeypatch)
    stats, results, baseline, project = _write_inputs(tmp_path)
    baseline.write_text(
        json.dumps(_baseline(reviewed_timeouts=[], timeout=0, source_root=tmp_path)),
        encoding="utf-8",
    )

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
            "--hypothesis-profile",
            "mutation",
            "--max-children",
            "4",
        ],
    )

    assert exit_code == 1
    error = capsys.readouterr().err
    assert "unexpected timeout mutants: services.alpha.x_second__mutmut_1" in error
    assert "mutation score regression" not in error
    assert "catalog drift" not in error
