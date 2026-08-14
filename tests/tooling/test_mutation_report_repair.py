"""Targeted-repair contracts for the Nightly mutation reporter."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.tooling import test_mutation_report as base
from tools import mutation_report


def test_reporter_supports_nightly_direct_script_invocation() -> None:
    project_root = Path(__file__).parents[2]
    completed = subprocess.run(
        [sys.executable, "tools/mutation_report.py", "--help"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "{report,gate}" in completed.stdout


def _incomplete_results() -> str:
    return (
        "    services.a.x_f__mutmut_1: killed\n"
        "    services.a.x_f__mutmut_2: survived\n"
        "    services.a.x_f__mutmut_3: not checked\n"
        "    services.a.x_f__mutmut_4: check was interrupted by user"
    )


def _repair_results(*, last_status: str = "timeout") -> str:
    # mutmut_1 (killed) and mutmut_2 (a reviewed survivor) are not selected, so
    # a targeted run leaves both reset to ``not checked``.
    return (
        "    services.a.x_f__mutmut_1: not checked\n"
        "    services.a.x_f__mutmut_2: not checked\n"
        "    services.a.x_f__mutmut_3: killed\n"
        f"    services.a.x_f__mutmut_4: {last_status}"
    )


def _incomplete_stats() -> dict[str, int]:
    return base._stats(
        killed=1,
        survived=1,
        timeout=0,
        total=4,
        check_was_interrupted_by_user=1,
    )


def test_targeted_repair_overlays_only_first_attempt_incomplete_identities() -> None:
    first = base._result_objects(_incomplete_results())
    timeout_name = "services.a.x_f__mutmut_4"
    report = base._build_report(
        _incomplete_stats(),
        first,
        base._baseline(
            results_text=_incomplete_results(),
            reviewed_timeouts=[timeout_name],
        ),
        base._result_objects(_repair_results()),
    )

    assert report["stats"] == _incomplete_stats()
    assert report["effective_stats"] == base._stats(killed=2, survived=1, timeout=1)
    assert report["stats_provenance"] == "derived_from_targeted_repair_overlay"
    assert [item["name"] for item in report["targeted_repairs"]] == [
        "services.a.x_f__mutmut_3",
        timeout_name,
    ]
    assert report["timeouts"] == [timeout_name]
    assert report["module_hotspots"] == [{"module": "services.a", "count": 2}]
    assert "Effective counts after targeted repair" in mutation_report.render_markdown(report)
    assert "Official first-attempt export" in mutation_report.render_markdown(report)


@pytest.mark.parametrize(
    "repair_text",
    [
        _repair_results().replace("    services.a.x_f__mutmut_4: timeout", ""),
        _repair_results().replace("x_f__mutmut_4", "x_f__mutmut_99"),
    ],
)
def test_targeted_repair_requires_the_same_full_catalog(repair_text: str) -> None:
    with pytest.raises(mutation_report.ReportError, match="catalog differs"):
        base._build_report(
            _incomplete_stats(),
            base._result_objects(_incomplete_results()),
            base._baseline(results_text=_incomplete_results()),
            base._result_objects(repair_text),
        )


@pytest.mark.parametrize("status", ["not checked", "check was interrupted by user"])
def test_targeted_repair_requires_every_requested_result_to_complete(status: str) -> None:
    with pytest.raises(mutation_report.ReportError, match="remains incomplete"):
        base._build_report(
            _incomplete_stats(),
            base._result_objects(_incomplete_results()),
            base._baseline(results_text=_incomplete_results()),
            base._result_objects(_repair_results(last_status=status)),
        )


def test_targeted_repair_rejects_duplicate_identity() -> None:
    repair = base._result_objects(_repair_results())
    repair[-1] = repair[0]
    with pytest.raises(mutation_report.ReportError, match="duplicate mutant in repair"):
        base._build_report(
            _incomplete_stats(),
            base._result_objects(_incomplete_results()),
            base._baseline(results_text=_incomplete_results()),
            repair,
        )


def test_targeted_repair_preserves_unexpected_timeout_gate() -> None:
    report = base._build_report(
        _incomplete_stats(),
        base._result_objects(_incomplete_results()),
        base._baseline(results_text=_incomplete_results(), reviewed_timeouts=[]),
        base._result_objects(_repair_results()),
    )

    assert report["unexpected_timeouts"] == ["services.a.x_f__mutmut_4"]
    assert report["meets_baseline"] is False


def test_targeted_repair_overlays_a_completed_first_attempt_timeout() -> None:
    first_text = (
        "    services.a.x_f__mutmut_1: killed\n"
        "    services.a.x_f__mutmut_2: timeout\n"
        "    services.a.x_f__mutmut_3: timeout"
    )
    # The workflow selected only mutmut_2. mutmut resets the non-selected
    # mutmut_1 and mutmut_3 rows, so the latter timeout must remain official.
    repair_text = (
        "    services.a.x_f__mutmut_1: not checked\n"
        "    services.a.x_f__mutmut_2: killed\n"
        "    services.a.x_f__mutmut_3: not checked"
    )
    retained_timeout = "services.a.x_f__mutmut_3"
    report = base._build_report(
        base._stats(killed=1, survived=0, timeout=2, total=3),
        base._result_objects(first_text),
        base._baseline(
            results_text=first_text,
            reviewed_timeouts=[retained_timeout],
            killed=2,
            survived=0,
            timeout=1,
            total=3,
        ),
        base._result_objects(repair_text),
    )

    assert report["effective_stats"] == base._stats(
        killed=2,
        survived=0,
        timeout=1,
        total=3,
    )
    assert report["timeouts"] == [retained_timeout]
    assert report["unexpected_timeouts"] == []
    assert report["targeted_repairs"] == [
        {
            "name": "services.a.x_f__mutmut_2",
            "first_status": "timeout",
            "repair_status": "killed",
        },
    ]


def _contended_survivor_inputs() -> tuple[str, str, str]:
    first_text = (
        "    services.a.x_f__mutmut_1: killed\n"
        "    services.a.x_f__mutmut_2: survived\n"
        "    services.a.x_f__mutmut_3: survived"
    )
    # The workflow selected only the unreviewed mutmut_2; mutmut resets the
    # non-selected rows, so the reviewed mutmut_3 survivor stays official.
    repair_text = (
        "    services.a.x_f__mutmut_1: not checked\n"
        "    services.a.x_f__mutmut_2: {status}\n"
        "    services.a.x_f__mutmut_3: not checked"
    )
    return first_text, repair_text, "services.a.x_f__mutmut_3"


def test_serial_recheck_of_a_contended_survivor_restores_the_baseline_score() -> None:
    first_text, repair_text, reviewed = _contended_survivor_inputs()
    report = base._build_report(
        base._stats(killed=1, survived=2, timeout=0, total=3),
        base._result_objects(first_text),
        base._baseline(
            results_text=first_text,
            reviewed_timeouts=[],
            reviewed_survivors=[reviewed],
            killed=2,
            timeout=0,
            total=3,
        ),
        base._result_objects(repair_text.format(status="killed")),
    )

    assert report["effective_stats"] == base._stats(killed=2, survived=1, timeout=0, total=3)
    assert report["unexpected_survivors"] == []
    assert report["meets_baseline"] is True
    assert report["targeted_repairs"] == [
        {
            "name": "services.a.x_f__mutmut_2",
            "first_status": "survived",
            "repair_status": "killed",
        },
    ]


def test_a_survivor_that_survives_the_serial_recheck_still_fails_the_gate() -> None:
    first_text, repair_text, reviewed = _contended_survivor_inputs()
    report = base._build_report(
        base._stats(killed=1, survived=2, timeout=0, total=3),
        base._result_objects(first_text),
        base._baseline(
            results_text=first_text,
            reviewed_timeouts=[],
            reviewed_survivors=[reviewed],
            killed=2,
            timeout=0,
            total=3,
        ),
        base._result_objects(repair_text.format(status="survived")),
    )

    assert report["unexpected_survivors"] == ["services.a.x_f__mutmut_2"]
    assert report["meets_baseline"] is False
    assert "Unexpected survivor mutants" in mutation_report.render_markdown(report)


@pytest.mark.parametrize("command", ["report", "gate"])
def test_report_and_gate_cli_accept_targeted_repair_results(command: str) -> None:
    arguments = [
        command,
        "--stats",
        "stats.json",
        "--results",
        "results.txt",
        "--repair-results",
        "repair.txt",
        "--baseline",
        "baseline.json",
        "--project",
        "pyproject.toml",
        "--hypothesis-profile",
        "mutation",
        "--max-children",
        "4",
        "--python-hash-seed",
        "0",
        "--timezone",
        "UTC",
    ]
    if command == "report":
        arguments.extend(["--output", "report.json", "--summary", "summary.md"])

    args = mutation_report.parser().parse_args(arguments)

    assert str(args.repair_results) == "repair.txt"
