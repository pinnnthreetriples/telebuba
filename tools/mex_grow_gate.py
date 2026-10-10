"""Claude Code Stop hook: ask once for MEX GROW when committed code skips memory.

AGENTS.md already says to run GROW at task end, but agents finish without it
often enough that an instruction alone is not a gate. This hook blocks the stop
ONCE per session and HEAD when the branch has commits ahead of its merge-base
with ``origin/main`` that touch code, while neither those commits nor the
working tree touch ``.mex/``. The agent then updates memory or says in one line
why none is needed; the next stop passes.

Stop fires at every turn end, so only committed work counts: uncommitted edits
in progress never trigger it. ``stop_hook_active`` short-circuits a loop. Any
git error, a missing ``origin/main`` or unreadable input exits 0 silently — a
broken gate must never trap the agent.

    python3 tools/mex_grow_gate.py < stop-hook-input.json
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

BASE_REF = "origin/main"
CODE_PREFIXES = ("api/", "core/", "services/", "schemas/", "frontend/src/", "tools/")
CODE_FILES = frozenset({"main.py"})
MEMORY_PREFIX = ".mex/"
MARKER_DIR = "mex-grow-prompted"

REASON = (
    "MEX GROW: this branch commits code but no .mex/ change. Before finishing, "
    "update the matching .mex context (or a pattern for recurring work) and bump "
    "its last_updated, record rationale with `mex log --type decision`, then run "
    "the AGENTS.md memory check (`mex-agent check --quiet`). If memory needs no "
    "change, say why in one line instead."
)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    ).stdout


def _is_code(path: str) -> bool:
    return path in CODE_FILES or path.startswith(CODE_PREFIXES)


def _marker(top: Path, session_id: str, head: str) -> Path:
    safe_session = re.sub(r"[^A-Za-z0-9_-]", "_", session_id)
    git_dir = Path(_git(top, "rev-parse", "--git-path", MARKER_DIR).strip())
    return top / git_dir / f"{safe_session}.{head}"


def should_block(cwd: Path, session_id: str) -> bool:
    """Decide and, when blocking, remember the prompt for this session and HEAD."""
    top = Path(_git(cwd, "rev-parse", "--show-toplevel").strip())
    head = _git(top, "rev-parse", "HEAD").strip()
    base = _git(top, "merge-base", "HEAD", BASE_REF).strip()
    touched = set(
        _git(top, "log", "--no-renames", "--name-only", "--format=", f"{base}..HEAD").splitlines()
    )
    if not any(_is_code(path) for path in touched):
        return False
    if any(path.startswith(MEMORY_PREFIX) for path in touched):
        return False
    if _git(top, "status", "--porcelain", "--", MEMORY_PREFIX).strip():
        return False
    marker = _marker(top, session_id, head)
    if marker.exists():
        return False
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    return True


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
        session_id = payload.get("session_id")
        if payload.get("stop_hook_active") or not isinstance(session_id, str) or not session_id:
            return 0
        if should_block(Path(payload.get("cwd") or "."), session_id):
            sys.stdout.write(json.dumps({"decision": "block", "reason": REASON}))
    except Exception:  # noqa: BLE001 - fail open: a broken gate must not trap the agent
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
