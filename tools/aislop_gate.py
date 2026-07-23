"""Zero-tolerance aislop gate: fail on any error OR warning.

aislop's ``ci`` command only fails (via exit code) on errors; project policy is
no warnings either, so this parses its JSON summary and fails when
errors + warnings > 0. aislop is an npm tool, so this needs Node.js (npx) on
PATH — it is wired as a dedicated CI job (with setup-node) and a pre-push hook.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

_NPM_PACKAGE = os.environ.get("AISLOP_NPM_PACKAGE", "aislop@0.10.2")
# ``frontend/`` is the React SPA, governed by its own gate set
# (eslint/tsc/vitest); it is not Python project code, so it is outside the
# AI-slop quality gate.
_EXCLUDE = ".venv,node_modules,.git,htmlcov,.serena,frontend,frontend/**"
# Path prefixes (POSIX) whose diagnostics are dropped from the recomputed gate.
_EXCLUDED_PREFIXES = ("frontend/",)
# Distribution names that differ from their import module, which aislop's
# hallucinated-import check can't map: argon2-cffi -> argon2, PyJWT -> jwt.
# Both are declared in pyproject.toml; only the names differ.
_KNOWN_IMPORT_ALIASES = ("argon2", "jwt")
# Vulnerable-dependency findings that are unfixable here and accepted as such.
# ``click`` (PYSEC-2026-2132, < 8.3.3) can't be bumped while semgrep pins
# ``click~=8.1.8`` (< 8.2); it's dev-tooling / uvicorn-CLI only, not a runtime
# request path. Mirrors the pip-audit ``--ignore-vuln`` in ci.yml; drop both once
# semgrep loosens its click pin.
_ACCEPTED_VULN_DEPS = ("click",)


def _is_known_import_alias(item: dict[str, object]) -> bool:
    if item.get("rule") != "ai-slop/hallucinated-import":
        return False
    message = str(item.get("message", ""))
    return any(f'"{name}"' in message for name in _KNOWN_IMPORT_ALIASES)


def _is_accepted_vuln_dep(item: dict[str, object]) -> bool:
    if item.get("rule") != "security/vulnerable-dependency":
        return False
    message = str(item.get("message", ""))
    return any(message.rstrip().endswith(f": {name}") for name in _ACCEPTED_VULN_DEPS)


def _is_config_default_url(item: dict[str, object]) -> bool:
    # The hardcoded-url rule targets URLs buried in business logic. In the
    # Settings module every URL is an env-overridable default (PROXY__*_BASE_URL,
    # validated HTTPS) — that is what a config default IS, not slop. Scope the
    # exception to that one file + rule.
    if item.get("rule") != "ai-slop/hardcoded-url":
        return False
    return str(item.get("filePath", "")).replace("\\", "/").endswith("core/config.py")


def main() -> int:
    npx = shutil.which("npx")
    if npx is None:
        sys.stderr.write("aislop gate: Node.js (npx) not found on PATH\n")
        return 127
    completed = subprocess.run(
        [
            npx,
            "--yes",
            "--package",
            _NPM_PACKAGE,
            "aislop",
            "ci",
            ".",
            "--exclude",
            _EXCLUDE,
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError:
        sys.stdout.write(completed.stdout)
        sys.stderr.write(completed.stderr)
        return completed.returncode or 2
    summary = report.get("summary", {})
    # aislop's ``--exclude`` is unreliable across platforms, so filter the
    # excluded prefixes here and recompute the gate from what remains — the
    # ``frontend/`` React app is not Python code.
    diagnostics = [
        item
        for item in report.get("diagnostics", [])
        if not str(item.get("filePath", "")).replace("\\", "/").startswith(_EXCLUDED_PREFIXES)
        and not _is_known_import_alias(item)
        and not _is_accepted_vuln_dep(item)
        and not _is_config_default_url(item)
    ]
    for item in diagnostics:
        sys.stdout.write(
            f"  {item.get('filePath')}:{item.get('line')} "
            f"[{item.get('severity')}] {item.get('rule')}: {item.get('message')}\n",
        )
    errors = sum(1 for item in diagnostics if item.get("severity") == "error")
    warnings = sum(1 for item in diagnostics if item.get("severity") == "warning")
    if errors or warnings:
        sys.stdout.write(f"aislop: gate failed — {errors} error(s), {warnings} warning(s)\n")
        return 1
    sys.stdout.write(f"aislop: clean ({summary.get('files')} files scanned)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
