"""Zero-tolerance aislop gate: fail on any error OR warning.

aislop's ``ci`` command only fails (via exit code) on errors; project policy is
no warnings either, so this parses its JSON summary and fails when
errors + warnings > 0. aislop is an npm tool, so this needs Node.js (npx) on
PATH — it is wired as a dedicated CI job (with setup-node) and a pre-push hook.

Scope, exclusions and telemetry live in ``.aislop/config.yml``, not here: the
``ci`` subcommand silently ignores ``--exclude``, so this module used to pass the
flag AND drop the frontend from the results afterwards — a correct verdict over a
file count that was never true. The config file is honoured, so both went away.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

_NPM_PACKAGE = os.environ.get("AISLOP_NPM_PACKAGE", "aislop@0.14.0")
# Vulnerable-dependency findings that are unfixable here and accepted as such.
# ``click`` (PYSEC-2026-2132, < 8.3.3) can't be bumped while semgrep pins
# ``click~=8.1.8`` (< 8.2); it's dev-tooling / uvicorn-CLI only, not a runtime
# request path. Mirrors the pip-audit ``--ignore-vuln`` in ci.yml; drop both once
# semgrep loosens its click pin.
_ACCEPTED_VULN_DEPS = ("click",)


def _is_accepted_vuln_dep(item: dict[str, object]) -> bool:
    if item.get("rule") != "security/vulnerable-dependency":
        return False
    message = str(item.get("message", ""))
    return any(message.rstrip().endswith(f": {name}") for name in _ACCEPTED_VULN_DEPS)


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
    # One exception survives in code rather than in config: the accepted vulnerable
    # dependency, which only ever surfaces on a network-capable run and so cannot be
    # verified away locally.
    diagnostics = [
        item for item in report.get("diagnostics", []) if not _is_accepted_vuln_dep(item)
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
