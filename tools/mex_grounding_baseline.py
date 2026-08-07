"""Carry the grounding baseline in git so CI can enforce GROUNDING_DRIFT.

`grounds_to` frontmatter pins a memory claim to a code symbol, but on its own it
only catches that symbol DISAPPEARING. To also catch its body changing under a
note that still describes the old behaviour, mex compares the node's current
`bodyHash` against a baseline row in `_mex_grounded_source` — a table inside
`.mex/graph.db`, which is ~94MB and gitignored, so CI rebuilds it empty every run
and the comparison silently never happens.

mex's own way to populate that table (`mex graph ground`) is gated behind an
interactive TTY prompt, so no CI job can reach it. This keeps the baseline in a
small tracked JSON instead and replays it into the freshly built graph.

Keeping it in git rather than an `actions/cache` entry is the point, not a
workaround: a cache is best-effort and evictable, so a miss would silently drop
the gate. A tracked file makes the baseline a REVIEWABLE artifact — when a PR
changes a grounded function it must either update the memory and re-run
`capture` (the new hash shows up in the diff, and the reviewer sees that the note
was re-checked against the new code) or fail CI.

`source` is written empty on purpose. The drift check reads only `body_hash`, and
copying twelve function bodies into the repo would duplicate the code that the
graph exists to stop us duplicating. `mex sync`'s grounding repair, which does
use `source`, is an interactive flow that rebuilds its own baseline anyway.

    python3 tools/mex_grounding_baseline.py capture   # after grounding or a reviewed change
    python3 tools/mex_grounding_baseline.py apply     # CI, after `mex graph`
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

_GRAPH = Path(".mex/graph.db")
_BASELINE = Path(".mex/grounding-baseline.json")
_TABLE = "_mex_grounded_source"
_WITH_ARG = 2  # argv length once a subcommand carries its own argument
_ENTRY = re.compile(r'-\s+node:\s*"([^"]+)"\s*\r?\n\s+fingerprint:\s*"([^"]+)"')
_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)

_UPSERT = (
    f"INSERT INTO {_TABLE} (scaffold_file, node_id, source, body_hash, fingerprint) "  # noqa: S608
    "VALUES (?, ?, '', ?, ?) ON CONFLICT(scaffold_file, node_id) DO UPDATE SET "
    "source=excluded.source, body_hash=excluded.body_hash, fingerprint=excluded.fingerprint"
)


def _say(message: str) -> None:
    sys.stdout.write(f"{message}\n")


def _fail(message: str) -> None:
    sys.stderr.write(f"{message}\n")


def _grounded() -> list[tuple[str, str, str]]:
    """Every (scaffold_file, node_id, fingerprint) declared in the scaffold.

    mex keys the baseline by the path relative to the PROJECT root (`checkGrounding`
    does `relative(projectRoot, filePath)`), so the `.mex/` prefix is part of the key.
    Dropping it fails silently: the lookup never matches and drift is skipped.
    """
    out: list[tuple[str, str, str]] = []
    for path in sorted(Path(".mex").rglob("*.md")):
        head = _FRONTMATTER.match(path.read_text(encoding="utf-8"))
        if head is None:
            continue
        key = path.as_posix()
        out.extend((key, node, fingerprint) for node, fingerprint in _ENTRY.findall(head.group(1)))
    return out


def _connect() -> sqlite3.Connection:
    if not _GRAPH.exists():
        _fail(f"{_GRAPH} missing — run `mex graph` first")
        raise SystemExit(1)
    connection = sqlite3.connect(_GRAPH)
    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if _TABLE not in tables:
        # Fail loudly: this table is mex's, and a rename upstream must not degrade
        # into a green run with the drift check quietly disabled.
        connection.close()
        _fail(f"{_GRAPH} has no {_TABLE}; mex's grounding schema changed")
        raise SystemExit(1)
    return connection


def capture() -> int:
    rows = []
    with closing(_connect()) as connection:
        for scaffold_file, node_id, _fingerprint in _grounded():
            # The fingerprint is NOT stored here. It already lives in the file's
            # `grounds_to`, and mex reads that copy first; duplicating ~3kB of minhash
            # per node would make this file 14x larger and give it a second place to go stale.
            node = connection.execute(
                "SELECT body_hash FROM nodes WHERE id = ?",
                (node_id,),
            ).fetchone()
            if node is None or not node[0]:
                _fail(f"no node/body_hash for {node_id} ({scaffold_file})")
                return 1
            rows.append({"scaffold_file": scaffold_file, "node_id": node_id, "body_hash": node[0]})
    _BASELINE.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _say(f"{_BASELINE}: {len(rows)} baseline(s)")
    return 0


def apply() -> int:
    if not _BASELINE.exists():
        _fail(f"{_BASELINE} missing — run `capture`")
        return 1
    rows = json.loads(_BASELINE.read_text(encoding="utf-8"))
    grounded = _grounded()
    declared = {(scaffold, node) for scaffold, node, _ in grounded}
    stored = {(row["scaffold_file"], row["node_id"]) for row in rows}
    if declared != stored:
        # A claim grounded without re-capturing would be enforced for existence but
        # not for drift, which is the failure this whole file exists to prevent.
        _fail(f"{_BASELINE} is stale — re-run `capture`.")
        if declared - stored:
            _fail(f"  grounded but not captured: {sorted(declared - stored)}")
        if stored - declared:
            _fail(f"  captured but no longer grounded: {sorted(stored - declared)}")
        return 1

    fingerprints = {(scaffold, node): fp for scaffold, node, fp in grounded}
    with closing(_connect()) as connection, connection:
        connection.executemany(
            _UPSERT,
            [
                (
                    row["scaffold_file"],
                    row["node_id"],
                    row["body_hash"],
                    fingerprints[(row["scaffold_file"], row["node_id"])],
                )
                for row in rows
            ],
        )
    _say(f"{_TABLE}: {len(rows)} baseline(s) applied")
    return 0


def _git(*args: str) -> str | None:
    """Run a read-only git command, or None when it has nothing to say."""
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout if result.returncode == 0 else None


def verify(base: str) -> int:
    """Refuse a re-capture that did not come with a rethink of the note.

    `apply` proves the baseline covers every grounded claim; it cannot tell whether
    the note is still TRUE. Re-running `capture` after changing a grounded function
    makes the drift check green again whatever the prose now says, so on its own the
    gate rewards the one move it exists to prevent: refresh the hash, ship the stale
    sentence.

    So a changed hash must arrive with a changed memory file. Not proof the words
    are right — nothing automated can be — but it puts the note in the diff, in
    front of the reviewer, which is where that judgement belongs.

    Only CHANGED hashes count. A newly grounded claim has no previous hash to
    contradict, and its file is under review anyway.
    """
    previous = _git("show", f"{base}:{_BASELINE.as_posix()}")
    if previous is None:
        _say(f"{_BASELINE} is new in this branch — nothing to compare against {base}")
        return 0
    was = {(row["scaffold_file"], row["node_id"]): row["body_hash"] for row in json.loads(previous)}
    now = {
        (row["scaffold_file"], row["node_id"]): row["body_hash"]
        for row in json.loads(_BASELINE.read_text(encoding="utf-8"))
    }
    rehashed = {
        scaffold
        for (scaffold, node), digest in now.items()
        if was.get((scaffold, node), digest) != digest
    }
    if not rehashed:
        _say("no grounded body changed in this branch")
        return 0

    touched = set((_git("diff", "--name-only", f"{base}...HEAD") or "").split())
    silent = sorted(file for file in rehashed if file not in touched)
    if silent:
        _fail("A grounded body changed but its memory file did not:")
        for file in silent:
            _fail(f"  {file}")
        _fail("Re-read the note against the new code and update it, or say why it still holds.")
        return 1
    _say(f"{len(rehashed)} re-captured file(s), each edited in this branch")
    return 0


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "capture":
        return capture()
    if command == "apply":
        return apply()
    if command == "verify":
        base = sys.argv[2] if len(sys.argv) > _WITH_ARG else "origin/main"
        return verify(base)
    _fail("usage: mex_grounding_baseline.py capture|apply|verify [base-ref]")
    return 2


if __name__ == "__main__":
    sys.exit(main())
