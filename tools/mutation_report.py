"""Build and gate the Nightly mutmut report from mutmut 3.6 outputs."""

# ruff: noqa: EM101, EM102, T201, TRY003

from __future__ import annotations

import argparse
import importlib.metadata
import json
import re
import sys
import tomllib
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

STAT_KEYS = (
    "killed",
    "survived",
    "total",
    "no_tests",
    "skipped",
    "suspicious",
    "timeout",
    "check_was_interrupted_by_user",
    "segfault",
)
RESULT_STATUSES = frozenset(
    {
        "killed",
        "survived",
        "no tests",
        "skipped",
        "suspicious",
        "timeout",
        "check was interrupted by user",
        "segfault",
        "caught by type check",
        "not checked",
    },
)
RESULT_RE = re.compile(r"^\s+(?P<name>\S+__mutmut_\d+): (?P<status>.+)$")
SCOPE_KEYS = (
    "source_paths",
    "paths_to_exclude",
    "mutate_only_covered_lines",
    "pytest_add_cli_args_test_selection",
    "pytest_add_cli_args",
    "also_copy",
)


class ReportError(ValueError):
    """The report input is missing, inconsistent, or unsupported."""


@dataclass(frozen=True)
class Result:
    name: str
    status: str

    @property
    def module(self) -> str:
        if ".x_" in self.name:
            return self.name.split(".x_", 1)[0]
        if ".xǁ" in self.name:
            return self.name.split(".xǁ", 1)[0]
        raise ReportError(f"unsupported mutant name: {self.name}")

    @property
    def function(self) -> str:
        if ".x_" in self.name:
            return self.name.split(".x_", 1)[1].rsplit("__mutmut_", 1)[0]
        if ".xǁ" in self.name:
            encoded = self.name.split(".x", 1)[1].rsplit("__mutmut_", 1)[0]
            parts = [part for part in encoded.split("ǁ") if part]
            if parts:
                return ".".join(parts)
        raise ReportError(f"unsupported mutant name: {self.name}")


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReportError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReportError(f"expected a JSON object in {path}")
    return value


def load_stats(path: Path) -> dict[str, int]:
    raw = _load_object(path)
    missing = set(STAT_KEYS) - raw.keys()
    extra = raw.keys() - set(STAT_KEYS)
    if missing or extra:
        raise ReportError(
            f"unexpected mutmut stats keys (missing={sorted(missing)}, extra={sorted(extra)})",
        )
    stats: dict[str, int] = {}
    for key in STAT_KEYS:
        value = raw[key]
        if type(value) is not int or value < 0:  # bool is not an accepted counter
            raise ReportError(f"stats.{key} must be a non-negative integer")
        stats[key] = value
    if stats["total"] <= 0:
        raise ReportError("stats.total must be positive")
    return stats


def load_results(path: Path) -> list[Result]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ReportError(f"cannot read results {path}: {exc}") from exc
    parsed: list[Result] = []
    seen: set[str] = set()
    for line_number, line in enumerate(lines, 1):
        if not line:
            continue
        match = RESULT_RE.fullmatch(line)
        if match is None:
            raise ReportError(f"unsupported mutmut results line {line_number}: {line!r}")
        result = Result(match["name"], match["status"])
        if result.status not in RESULT_STATUSES:
            raise ReportError(f"unsupported status on line {line_number}: {result.status!r}")
        if result.name in seen:
            raise ReportError(f"duplicate mutant in results: {result.name}")
        seen.add(result.name)
        parsed.append(result)
    if not parsed:
        raise ReportError("mutmut results are empty")
    return parsed


def load_baseline(path: Path) -> dict[str, Any]:
    raw = _load_object(path)
    if set(raw) != {"mutmut_version", "mutmut_config", "stats"}:
        raise ReportError("baseline must contain exactly mutmut_version, mutmut_config, and stats")
    if not isinstance(raw["mutmut_version"], str) or not raw["mutmut_version"]:
        raise ReportError("baseline.mutmut_version must be a non-empty string")
    config = raw["mutmut_config"]
    if not isinstance(config, dict) or set(config) != set(SCOPE_KEYS):
        raise ReportError(f"baseline.mutmut_config must contain exactly {list(SCOPE_KEYS)!r}")
    expected_stats = {"killed", "survived", "timeout", "total"}
    stats = raw["stats"]
    if not isinstance(stats, dict) or set(stats) != expected_stats:
        raise ReportError("baseline.stats has unsupported keys")
    if any(type(value) is not int or value < 0 for value in stats.values()):
        raise ReportError("baseline stats must be non-negative integers")
    if stats["total"] <= 0:
        raise ReportError("baseline total must be positive")
    return raw


def validate_baseline_integrity(baseline: dict[str, Any], project_path: Path) -> None:
    try:
        project = tomllib.loads(project_path.read_text(encoding="utf-8"))
        project_config = project["tool"]["mutmut"]
    except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        raise ReportError(
            f"cannot read tool.mutmut from {project_path}: {exc}",
        ) from exc
    current_config = {key: project_config.get(key) for key in SCOPE_KEYS}
    if current_config != baseline["mutmut_config"]:
        raise ReportError(
            f"baseline mutation config {baseline['mutmut_config']!r} does not match "
            f"project config {current_config!r}",
        )
    try:
        installed_version = importlib.metadata.version("mutmut")
    except importlib.metadata.PackageNotFoundError as exc:
        raise ReportError("mutmut is not installed") from exc
    if installed_version != baseline["mutmut_version"]:
        raise ReportError(
            f"baseline mutmut version {baseline['mutmut_version']} does not match "
            f"installed {installed_version}",
        )


def _score(killed: int, total: int) -> Decimal:
    return Decimal(killed) * Decimal(100) / Decimal(total)


def _hotspots(results: list[Result], field: str, limit: int = 20) -> list[dict[str, Any]]:
    actionable = [item for item in results if item.status in {"survived", "timeout"}]
    if field == "function":
        counts = Counter(f"{item.module}::{item.function}" for item in actionable)
    else:
        counts = Counter(getattr(item, field) for item in actionable)
    return [{field: name, "count": count} for name, count in counts.most_common(limit)]


def build_report(
    stats: dict[str, int],
    results: list[Result],
    baseline: dict[str, Any],
) -> dict[str, Any]:
    if len(results) != stats["total"]:
        raise ReportError(f"results list has {len(results)} mutants, stats report {stats['total']}")
    result_counts = Counter(item.status.replace(" ", "_") for item in results)
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
    if result_counts["not_checked"] or result_counts["check_was_interrupted_by_user"]:
        raise ReportError("mutmut run is incomplete")
    exported_total = sum(stats[key] for key in STAT_KEYS if key != "total")
    omitted_total = result_counts["caught_by_type_check"]
    if exported_total + omitted_total != stats["total"]:
        raise ReportError(
            "official stats plus caught-by-type-check results do not account for total",
        )
    current_score = _score(stats["killed"], stats["total"])
    base = baseline["stats"]
    baseline_score = _score(base["killed"], base["total"])
    timeouts = [item.name for item in results if item.status == "timeout"]
    return {
        "stats": stats,
        "score_percent": str(current_score.quantize(Decimal("0.0001"))),
        "baseline": baseline,
        "baseline_score_percent": str(baseline_score.quantize(Decimal("0.0001"))),
        "delta_percentage_points": str(
            (current_score - baseline_score).quantize(Decimal("0.0001")),
        ),
        "meets_baseline": stats["killed"] * base["total"] >= base["killed"] * stats["total"],
        "module_hotspots": _hotspots(results, "module"),
        "function_hotspots": _hotspots(results, "function"),
        "timeouts": timeouts,
    }


def render_markdown(report: dict[str, Any]) -> str:
    stats = report["stats"]
    lines = [
        "## Mutation testing",
        "",
        f"Score: **{report['score_percent']}%** "
        f"({report['delta_percentage_points']} pp vs baseline "
        f"{report['baseline_score_percent']}%).",
        "",
        f"Killed: {stats['killed']} · Survived: {stats['survived']} · "
        f"Timeout: {stats['timeout']} · Total: {stats['total']}",
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
    lines.extend(["", "### Timeout mutants", ""])
    if report["timeouts"]:
        lines.extend(f"- `{name}`" for name in report["timeouts"])
    else:
        lines.append("None.")
    return "\n".join(lines) + "\n"


def _inputs(args: argparse.Namespace) -> tuple[dict[str, int], list[Result], dict[str, Any]]:
    baseline = load_baseline(args.baseline)
    validate_baseline_integrity(baseline, args.project)
    return load_stats(args.stats), load_results(args.results), baseline


def report_command(args: argparse.Namespace) -> int:
    stats, results, baseline = _inputs(args)
    report = build_report(stats, results, baseline)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    markdown = render_markdown(report)
    args.summary.write_text(markdown, encoding="utf-8")
    return 0


def gate_command(args: argparse.Namespace) -> int:
    stats, results, baseline = _inputs(args)
    report = build_report(stats, results, baseline)
    if report["meets_baseline"]:
        print(f"mutation score {report['score_percent']}% meets baseline")
        return 0
    print(
        f"mutation score regression: {report['score_percent']}% < "
        f"{report['baseline_score_percent']}% baseline",
        file=sys.stderr,
    )
    return 1


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser()
    subparsers = cli.add_subparsers(dest="command", required=True)
    for command in ("report", "gate"):
        sub = subparsers.add_parser(command)
        sub.add_argument("--stats", type=Path, required=True)
        sub.add_argument("--results", type=Path, required=True)
        sub.add_argument("--baseline", type=Path, required=True)
        sub.add_argument("--project", type=Path, required=True)
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
