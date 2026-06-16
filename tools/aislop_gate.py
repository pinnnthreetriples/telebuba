"""Zero-tolerance aislop gate: fail on any error OR warning.

aislop's ``ci`` command only fails (via exit code) on errors; project policy is
no warnings either, so this parses its JSON summary and fails when
errors + warnings > 0. aislop is an npm tool, so this needs Node.js (npx) on
PATH — it is wired as a dedicated CI job (with setup-node) and a pre-push hook.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

_NPM_PACKAGE = "aislop@0.10.2"
_EXCLUDE = ".venv,node_modules,.git,htmlcov,.serena"


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
    errors = int(summary.get("errors", 0))
    warnings = int(summary.get("warnings", 0))
    for item in report.get("diagnostics", []):
        sys.stdout.write(
            f"  {item.get('filePath')}:{item.get('line')} "
            f"[{item.get('severity')}] {item.get('rule')}: {item.get('message')}\n",
        )
    if errors or warnings:
        sys.stdout.write(f"aislop: gate failed — {errors} error(s), {warnings} warning(s)\n")
        return 1
    sys.stdout.write(f"aislop: clean ({summary.get('files')} files scanned)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
