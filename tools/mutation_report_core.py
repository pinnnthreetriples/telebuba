"""Validated mutmut inputs and semantic catalogue identity for Nightly reporting."""

# ruff: noqa: EM101, EM102, TRY003

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

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
INCOMPLETE_STATUSES = frozenset({"not checked", "check was interrupted by user"})
RESULT_RE = re.compile(r"^\s+(?P<name>\S+__mutmut_\d+): (?P<status>.+)$")
MUTANT_NAME_RE = re.compile(r"^\S+__mutmut_\d+$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SCOPE_KEYS = (
    "source_paths",
    "only_mutate",
    "do_not_mutate",
    "do_not_mutate_patterns",
    "max_stack_depth",
    "mutate_only_covered_lines",
    "pytest_add_cli_args_test_selection",
    "pytest_add_cli_args",
    "also_copy",
    "timeout_multiplier",
    "timeout_constant",
    "type_check_command",
)
CONFIG_DEFAULTS: dict[str, Any] = {
    "source_paths": None,
    "only_mutate": [],
    "do_not_mutate": [],
    "do_not_mutate_patterns": [],
    "max_stack_depth": -1,
    "mutate_only_covered_lines": False,
    "pytest_add_cli_args_test_selection": [],
    "pytest_add_cli_args": [],
    "also_copy": [],
    "timeout_multiplier": 15.0,
    "timeout_constant": 1.0,
    "type_check_command": [],
}
BASELINE_KEYS = frozenset(
    {
        "hypothesis_profile",
        "max_children",
        "python_hash_seed",
        "timezone",
        "mutmut_version",
        "python_version",
        "mutant_catalog_sha256",
        "reviewed_timeouts",
        "mutmut_config",
        "stats",
    },
)


class ReportError(ValueError):
    """The report input is missing, inconsistent, or unsupported."""


class Digest(Protocol):
    """Minimal hashlib-compatible interface used by the catalogue encoder."""

    def update(self, data: bytes, /) -> None:
        """Add bytes to the digest."""


@dataclass(frozen=True)
class Result:
    """One named mutant and its mutmut result status."""

    name: str
    status: str

    @property
    def module(self) -> str:
        """Return the production module encoded in a mutmut mutant name."""
        if ".x_" in self.name:
            return self.name.split(".x_", 1)[0]
        if ".xǁ" in self.name:
            return self.name.split(".xǁ", 1)[0]
        raise ReportError(f"unsupported mutant name: {self.name}")

    @property
    def function(self) -> str:
        """Return the function or method encoded in a mutmut mutant name."""
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
    """Load and strictly validate mutmut's CI/CD statistics export."""
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
        if type(value) is not int or value < 0:
            raise ReportError(f"stats.{key} must be a non-negative integer")
        stats[key] = value
    if stats["total"] <= 0:
        raise ReportError("stats.total must be positive")
    return stats


def load_results(path: Path) -> list[Result]:
    """Load the complete raw `mutmut results --all true` output."""
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


def _validate_reviewed_timeouts(value: object) -> list[str]:
    reviewed_timeouts = value
    valid_names = isinstance(reviewed_timeouts, list) and all(
        isinstance(name, str) and MUTANT_NAME_RE.fullmatch(name) is not None
        for name in reviewed_timeouts
    )
    if not valid_names:
        raise ReportError("baseline.reviewed_timeouts must be a list of mutant names")
    if len(reviewed_timeouts) != len(set(reviewed_timeouts)):
        raise ReportError("baseline.reviewed_timeouts must not contain duplicates")
    if reviewed_timeouts != sorted(reviewed_timeouts):
        raise ReportError("baseline.reviewed_timeouts must be sorted")
    return cast("list[str]", reviewed_timeouts)


def _validate_measurement_identity(raw: dict[str, Any]) -> list[str]:
    invalid_string_keys = [
        key
        for key in (
            "mutmut_version",
            "python_version",
            "hypothesis_profile",
            "python_hash_seed",
            "timezone",
        )
        if not isinstance(raw[key], str) or not raw[key]
    ]
    if invalid_string_keys:
        raise ReportError(f"baseline.{invalid_string_keys[0]} must be a non-empty string")
    if type(raw["max_children"]) is not int or raw["max_children"] <= 0:
        raise ReportError("baseline.max_children must be a positive integer")
    catalog_digest = raw["mutant_catalog_sha256"]
    if not isinstance(catalog_digest, str) or SHA256_RE.fullmatch(catalog_digest) is None:
        raise ReportError("baseline.mutant_catalog_sha256 must be a lowercase SHA-256 digest")
    return _validate_reviewed_timeouts(raw["reviewed_timeouts"])


def _validate_baseline_stats(value: object, reviewed_timeouts: list[str]) -> None:
    stats = value
    expected_stats = {"killed", "survived", "timeout", "total"}
    if not isinstance(stats, dict) or set(stats) != expected_stats:
        raise ReportError("baseline.stats has unsupported keys")
    if any(type(counter) is not int or counter < 0 for counter in stats.values()):
        raise ReportError("baseline stats must be non-negative integers")
    stats = cast("dict[str, int]", stats)
    if stats["total"] <= 0:
        raise ReportError("baseline total must be positive")
    measured_keys = ("killed", "survived", "timeout")
    if any(stats[key] > stats["total"] for key in measured_keys):
        raise ReportError("baseline counters must not exceed total")
    if sum(stats[key] for key in measured_keys) > stats["total"]:
        raise ReportError("baseline killed, survived, and timeout exceed total")
    if stats["timeout"] != len(reviewed_timeouts):
        raise ReportError("baseline timeout count must match reviewed_timeouts")


def load_baseline(path: Path) -> dict[str, Any]:
    """Load the pinned mutation measurement contract."""
    raw = _load_object(path)
    if set(raw) != BASELINE_KEYS:
        raise ReportError(f"baseline must contain exactly {sorted(BASELINE_KEYS)!r}")
    reviewed_timeouts = _validate_measurement_identity(raw)
    config = raw["mutmut_config"]
    if not isinstance(config, dict) or set(config) != set(SCOPE_KEYS):
        raise ReportError(f"baseline.mutmut_config must contain exactly {list(SCOPE_KEYS)!r}")
    _validate_baseline_stats(raw["stats"], reviewed_timeouts)
    return raw


def validate_baseline_integrity(  # noqa: PLR0913
    baseline: dict[str, Any],
    project_path: Path,
    hypothesis_profile: str,
    max_children: int,
    python_hash_seed: str = "0",
    timezone: str = "UTC",
) -> None:
    """Validate all environment and configuration pins against the baseline."""
    try:
        project = tomllib.loads(project_path.read_text(encoding="utf-8"))
        project_config = project["tool"]["mutmut"]
    except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        raise ReportError(f"cannot read tool.mutmut from {project_path}: {exc}") from exc
    current_config = {key: project_config.get(key, CONFIG_DEFAULTS[key]) for key in SCOPE_KEYS}
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
    current_python = platform.python_version()
    if current_python != baseline["python_version"]:
        raise ReportError(
            f"baseline Python version {baseline['python_version']} does not match "
            f"installed {current_python}",
        )
    if hypothesis_profile != baseline["hypothesis_profile"]:
        raise ReportError(
            f"baseline Hypothesis profile {baseline['hypothesis_profile']!r} does not match "
            f"requested {hypothesis_profile!r}",
        )
    if max_children != baseline["max_children"]:
        raise ReportError(
            f"baseline max_children {baseline['max_children']} does not match "
            f"requested {max_children}",
        )
    if python_hash_seed != baseline["python_hash_seed"]:
        raise ReportError(
            f"baseline Python hash seed {baseline['python_hash_seed']!r} does not match "
            f"requested {python_hash_seed!r}",
        )
    if timezone != baseline["timezone"]:
        raise ReportError(
            f"baseline timezone {baseline['timezone']!r} does not match requested {timezone!r}",
        )


def _hash_value(digest: Digest, value: bytes) -> None:
    digest.update(len(value).to_bytes(8, byteorder="big"))
    digest.update(value)


def _resolve_source_path(root: Path, configured_path: str) -> Path:
    if Path(configured_path).is_absolute():
        raise ReportError(f"source path must be relative: {configured_path!r}")
    candidate = (root / configured_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ReportError(f"source path escapes project root: {configured_path!r}") from exc
    if not candidate.exists():
        raise ReportError(f"configured source path does not exist: {configured_path!r}")
    return candidate


def _read_python_sources(root: Path, candidate: Path) -> dict[str, bytes]:
    paths = [candidate] if candidate.is_file() else candidate.rglob("*.py")
    files: dict[str, bytes] = {}
    for path in paths:
        if not path.is_file() or path.suffix != ".py":
            continue
        relative = path.relative_to(root).as_posix()
        try:
            files[relative] = path.read_bytes()
        except OSError as exc:
            raise ReportError(f"cannot read source file {path}: {exc}") from exc
    return files


def _source_files(source_root: Path, source_paths: list[str]) -> list[tuple[str, bytes]]:
    root = source_root.resolve()
    if not source_paths or any(not isinstance(path, str) or not path for path in source_paths):
        raise ReportError("mutmut source_paths must be a non-empty list of paths")
    files: dict[str, bytes] = {}
    for configured_path in source_paths:
        candidate = _resolve_source_path(root, configured_path)
        files.update(_read_python_sources(root, candidate))
    if not files:
        raise ReportError("configured source paths contain no Python source files")
    return sorted(files.items())


def mutant_catalog_sha256(
    results: list[Result],
    source_root: Path,
    source_paths: list[str],
) -> str:
    """Bind mutant names to stable paths and bytes of their measured source tree."""
    digest = hashlib.sha256()
    digest.update(b"telebuba-mutant-catalog-v2\0")
    for name in sorted(item.name for item in results):
        _hash_value(digest, name.encode("utf-8"))
    digest.update(b"\0source-tree\0")
    for relative_path, contents in _source_files(source_root, source_paths):
        _hash_value(digest, relative_path.encode("utf-8"))
        _hash_value(digest, contents)
    return digest.hexdigest()
