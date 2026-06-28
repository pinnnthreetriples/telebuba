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
# ``web/`` holds the vendored design SPA (served verbatim) and ``frontend/`` is
# the React SPA, governed by its own gate set (eslint/tsc/vitest); neither is
# Python project code, so both are outside the AI-slop quality gate.
_EXCLUDE = ".venv,node_modules,.git,htmlcov,.serena,web,web/**,frontend,frontend/**"
# Path prefixes (POSIX) whose diagnostics are dropped from the recomputed gate.
_EXCLUDED_PREFIXES = ("web/", "frontend/")
# Distribution names that differ from their import module, which aislop's
# hallucinated-import check can't map: argon2-cffi -> argon2, PyJWT -> jwt.
# Both are declared in pyproject.toml; only the names differ.
_KNOWN_IMPORT_ALIASES = ("argon2", "jwt")


def _is_known_import_alias(item: dict[str, object]) -> bool:
    if item.get("rule") != "ai-slop/hallucinated-import":
        return False
    message = str(item.get("message", ""))
    return any(f'"{name}"' in message for name in _KNOWN_IMPORT_ALIASES)


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
    # vendored ``web/`` SPA and the ``frontend/`` React app are not Python code.
    diagnostics = [
        item
        for item in report.get("diagnostics", [])
        if not str(item.get("filePath", "")).replace("\\", "/").startswith(_EXCLUDED_PREFIXES)
        and not _is_known_import_alias(item)
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
