"""Build and gate the Nightly mutmut report from mutmut 3.6 outputs."""

# ruff: noqa: EM101, EM102, T201, TRY003

from __future__ import annotations

import argparse
import importlib as _importlib
import json
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    _core = _importlib.import_module("mutation_report_core")
else:
    from . import mutation_report_core as _core

INCOMPLETE_STATUSES = _core.INCOMPLETE_STATUSES
STAT_KEYS = _core.STAT_KEYS
ReportError = _core.ReportError
Result = _core.Result
load_baseline = _core.load_baseline
load_results = _core.load_results
load_stats = _core.load_stats
mutant_catalog_sha256 = _core.mutant_catalog_sha256
validate_baseline_integrity = _core.validate_baseline_integrity

importlib = _core.importlib
platform = _core.platform


def _score(killed: int, total: int) -> Decimal:
    return Decimal(killed) * Decimal(100) / Decimal(total)


def _hotspots(results: list[Result], field: str, limit: int = 20) -> list[dict[str, Any]]:
    actionable = [item for item in results if item.status in {"survived", "timeout"}]
    if field == "function":
        counts = Counter(f"{item.module}::{item.function}" for item in actionable)
    else:
        counts = Counter(getattr(item, field) for item in actionable)
    return [{field: name, "count": count} for name, count in counts.most_common(limit)]


def _result_counts(results: list[Result]) -> Counter[str]:
    return Counter(item.status.replace(" ", "_") for item in results)


def _validate_raw_results(stats: dict[str, int], results: list[Result]) -> Counter[str]:
    if len(results) != stats["total"]:
        raise ReportError(f"results list has {len(results)} mutants, stats report {stats['total']}")
    if len({item.name for item in results}) != len(results):
        raise ReportError("duplicate mutant in results")
    result_counts = _result_counts(results)
    for key in (
        "killed",
        "survived",
        "timeout",
        "no_tests",
        "skipped",
        "suspicious",
        "segfault",
        "check_was_interrupted_by_user",
    ):
        if result_counts[key] != stats[key]:
            raise ReportError(
                f"results/status mismatch for {key}: {result_counts[key]} != {stats[key]}",
            )
    exported_total = sum(stats[key] for key in STAT_KEYS if key != "total")
    omitted_total = result_counts["caught_by_type_check"] + result_counts["not_checked"]
    if exported_total + omitted_total != stats["total"]:
        raise ReportError("official stats plus omitted results do not account for total")
    return result_counts


def _overlay_targeted_repairs(
    first_results: list[Result],
    repair_results: list[Result],
) -> tuple[list[Result], list[dict[str, str]]]:
    repair_by_name = _validated_repair_index(first_results, repair_results)
    requested_names = {item.name for item in first_results if item.status in INCOMPLETE_STATUSES}
    repairs: list[dict[str, str]] = []
    effective: list[Result] = []
    for first in first_results:
        if first.name not in requested_names:
            effective.append(first)
            continue
        repair = repair_by_name[first.name]
        if repair.status in INCOMPLETE_STATUSES:
            raise ReportError(
                f"targeted repair remains incomplete: {repair.name}: {repair.status}",
            )
        effective.append(repair)
        repairs.append(
            {
                "name": first.name,
                "first_status": first.status,
                "repair_status": repair.status,
            },
        )
    return effective, repairs


def _validated_repair_index(
    first_results: list[Result],
    repair_results: list[Result],
) -> dict[str, Result]:
    first_by_name = {item.name: item for item in first_results}
    repair_by_name = {item.name: item for item in repair_results}
    if len(first_by_name) != len(first_results):
        raise ReportError("duplicate mutant in first-attempt results")
    if len(repair_by_name) != len(repair_results):
        raise ReportError("duplicate mutant in repair results")
    first_names = set(first_by_name)
    repair_names = set(repair_by_name)
    if repair_names != first_names:
        missing = sorted(first_names - repair_names)
        unknown = sorted(repair_names - first_names)
        raise ReportError(f"repair mutant catalog differs (missing={missing}, unknown={unknown})")
    return repair_by_name


def _derived_stats(results: list[Result]) -> dict[str, int]:
    counts = _result_counts(results)
    return {key: len(results) if key == "total" else counts[key] for key in STAT_KEYS}


def _validate_effective_results(results: list[Result]) -> None:
    counts = _result_counts(results)
    if any(counts[status.replace(" ", "_")] for status in INCOMPLETE_STATUSES):
        raise ReportError("mutmut run is incomplete after targeted repair")
    allowed = {"killed", "survived", "timeout"}
    invalid = [item for item in results if item.status not in allowed]
    if invalid:
        details = ", ".join(f"{item.name}: {item.status}" for item in invalid)
        raise ReportError(f"infrastructure-invalid effective mutant statuses: {details}")


def _effective_measurement(
    stats: dict[str, int],
    results: list[Result],
    repair_results: list[Result] | None,
) -> tuple[list[Result], dict[str, int], list[dict[str, str]] | None]:
    raw_counts = _validate_raw_results(stats, results)
    raw_incomplete = any(raw_counts[status.replace(" ", "_")] for status in INCOMPLETE_STATUSES)
    if repair_results is None:
        if raw_incomplete:
            raise ReportError("mutmut run is incomplete")
        effective_results, effective_stats, repairs = results, stats, None
    else:
        effective_results, repairs = _overlay_targeted_repairs(results, repair_results)
        effective_stats = _derived_stats(effective_results)
    _validate_effective_results(effective_results)
    return effective_results, effective_stats, repairs


def _timeout_summary(
    results: list[Result],
    reviewed_timeout_names: list[str],
) -> tuple[list[str], list[str], list[str]]:
    timeouts = [item.name for item in results if item.status == "timeout"]
    timeout_names = set(timeouts)
    reviewed = set(reviewed_timeout_names)
    unexpected = [name for name in timeouts if name not in reviewed]
    resolved = [name for name in reviewed_timeout_names if name not in timeout_names]
    return timeouts, unexpected, resolved


def build_report(
    stats: dict[str, int],
    results: list[Result],
    baseline: dict[str, Any],
    repair_results: list[Result] | None = None,
    *,
    source_root: Path,
) -> dict[str, Any]:
    effective_results, effective_stats, repairs = _effective_measurement(
        stats,
        results,
        repair_results,
    )
    current_score = _score(effective_stats["killed"], effective_stats["total"])
    base = baseline["stats"]
    baseline_score = _score(base["killed"], base["total"])
    timeouts, unexpected_timeouts, resolved_reviewed_timeouts = _timeout_summary(
        effective_results,
        baseline["reviewed_timeouts"],
    )
    catalog_digest = mutant_catalog_sha256(
        results,
        source_root,
        baseline["mutmut_config"]["source_paths"],
    )
    catalog_matches_baseline = catalog_digest == baseline["mutant_catalog_sha256"]
    meets_score_baseline = (
        effective_stats["killed"] * base["total"] >= base["killed"] * effective_stats["total"]
    )
    report = {
        "stats": stats,
        "score_percent": str(current_score.quantize(Decimal("0.0001"))),
        "baseline": baseline,
        "baseline_score_percent": str(baseline_score.quantize(Decimal("0.0001"))),
        "delta_percentage_points": str(
            (current_score - baseline_score).quantize(Decimal("0.0001")),
        ),
        "meets_score_baseline": meets_score_baseline,
        "meets_baseline": (
            meets_score_baseline and catalog_matches_baseline and not unexpected_timeouts
        ),
        "mutant_catalog_sha256": catalog_digest,
        "catalog_matches_baseline": catalog_matches_baseline,
        "module_hotspots": _hotspots(effective_results, "module"),
        "function_hotspots": _hotspots(effective_results, "function"),
        "timeouts": timeouts,
        "unexpected_timeouts": unexpected_timeouts,
        "resolved_reviewed_timeouts": resolved_reviewed_timeouts,
    }
    if repairs is not None:
        report.update(
            {
                "stats_provenance": "derived_from_targeted_repair_overlay",
                "effective_stats": effective_stats,
                "targeted_repairs": repairs,
            },
        )
    return report


def _count_lines(report: dict[str, Any], stats: dict[str, int]) -> list[str]:
    if report.get("stats_provenance") == "derived_from_targeted_repair_overlay":
        raw = report["stats"]
        return [
            f"Effective counts after targeted repair — Killed: {stats['killed']} · "
            f"Survived: {stats['survived']} · Timeout: {stats['timeout']} · "
            f"Total: {stats['total']}",
            "",
            f"Official first-attempt export — Killed: {raw['killed']} · "
            f"Survived: {raw['survived']} · Timeout: {raw['timeout']} · "
            f"Interrupted: {raw['check_was_interrupted_by_user']} · Total: {raw['total']}",
            "",
            f"Targeted repairs applied: {len(report['targeted_repairs'])}.",
        ]
    return [
        f"Killed: {stats['killed']} · Survived: {stats['survived']} · "
        f"Timeout: {stats['timeout']} · Total: {stats['total']}",
    ]


def _append_name_section(lines: list[str], title: str, names: list[str]) -> None:
    lines.extend(["", f"### {title}", ""])
    lines.extend((f"- `{name}`" for name in names) if names else ["None."])


def render_markdown(report: dict[str, Any]) -> str:
    stats = report.get("effective_stats", report["stats"])
    catalog_status = "matches baseline" if report["catalog_matches_baseline"] else "drifted"
    lines = [
        "## Mutation testing",
        "",
        f"Score: **{report['score_percent']}%** "
        f"({report['delta_percentage_points']} pp vs baseline "
        f"{report['baseline_score_percent']}%).",
        "",
        *_count_lines(report, stats),
        "",
        f"Mutant catalog: **{catalog_status}** (`{report['mutant_catalog_sha256']}`).",
        "",
        "### Module hotspots",
        "",
        "| Module | Survived + timeout |",
        "|---|---:|",
    ]
    lines.extend(f"| `{item['module']}` | {item['count']} |" for item in report["module_hotspots"])
    lines.extend(
        ["", "### Function hotspots", "", "| Function | Survived + timeout |", "|---|---:|"],
    )
    lines.extend(
        f"| `{item['function']}` | {item['count']} |" for item in report["function_hotspots"]
    )
    _append_name_section(lines, "Timeout mutants", report["timeouts"])
    _append_name_section(lines, "Unexpected timeout mutants", report["unexpected_timeouts"])
    if report["resolved_reviewed_timeouts"]:
        lines.extend(["", "### Resolved reviewed timeout mutants", ""])
        lines.extend(f"- `{name}`" for name in report["resolved_reviewed_timeouts"])
    return "\n".join(lines) + "\n"


def _inputs(
    args: argparse.Namespace,
) -> tuple[dict[str, int], list[Result], dict[str, Any], list[Result] | None]:
    baseline = load_baseline(args.baseline)
    validate_baseline_integrity(
        baseline,
        args.project,
        args.hypothesis_profile,
        args.max_children,
    )
    repair_results = load_results(args.repair_results) if args.repair_results else None
    return load_stats(args.stats), load_results(args.results), baseline, repair_results


def report_command(args: argparse.Namespace) -> int:
    stats, results, baseline, repair_results = _inputs(args)
    report = build_report(
        stats,
        results,
        baseline,
        repair_results,
        source_root=args.project.parent,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    markdown = render_markdown(report)
    args.summary.write_text(markdown, encoding="utf-8")
    return 0


def gate_command(args: argparse.Namespace) -> int:
    stats, results, baseline, repair_results = _inputs(args)
    report = build_report(
        stats,
        results,
        baseline,
        repair_results,
        source_root=args.project.parent,
    )
    failed = False
    if not report["catalog_matches_baseline"]:
        print(
            "mutant catalog drift: "
            f"{report['mutant_catalog_sha256']} != "
            f"{baseline['mutant_catalog_sha256']}",
            file=sys.stderr,
        )
        failed = True
    if report["unexpected_timeouts"]:
        print(
            "unexpected timeout mutants: " + ", ".join(report["unexpected_timeouts"]),
            file=sys.stderr,
        )
        failed = True
    if not report["meets_score_baseline"]:
        print(
            f"mutation score regression: {report['score_percent']}% < "
            f"{report['baseline_score_percent']}% baseline",
            file=sys.stderr,
        )
        failed = True
    if failed:
        return 1
    print(f"mutation score {report['score_percent']}% meets baseline")
    return 0


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser()
    subparsers = cli.add_subparsers(dest="command", required=True)
    for command in ("report", "gate"):
        sub = subparsers.add_parser(command)
        sub.add_argument("--stats", type=Path, required=True)
        sub.add_argument("--results", type=Path, required=True)
        sub.add_argument("--repair-results", type=Path)
        sub.add_argument("--baseline", type=Path, required=True)
        sub.add_argument("--project", type=Path, required=True)
        sub.add_argument("--hypothesis-profile", required=True)
        sub.add_argument("--max-children", type=int, required=True)
        if command == "report":
            sub.add_argument("--output", type=Path, required=True)
            sub.add_argument("--summary", type=Path, required=True)
            sub.set_defaults(handler=report_command)
        else:
            sub.set_defaults(handler=gate_command)
    return cli


def main(argv: list[str] | None = None) -> int:
    cli = parser()
    args = cli.parse_args(argv)
    try:
        return args.handler(args)
    except ReportError as exc:
        cli.error(f"mutation report error: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
