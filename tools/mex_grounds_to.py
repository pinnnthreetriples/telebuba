"""Print the `grounds_to` frontmatter entry for one code-graph node.

`grounds_to` pins a memory claim to a code symbol. Each entry carries three
fields: `node`, a `fingerprint` that lets mex reconcile a moved symbol, and the
`bodyHash` that mex compares against the node's current body to raise
GROUNDING_DRIFT. Without `bodyHash` mex has nothing to compare against and the
drift check silently never happens, so an entry is only complete with all three.

mex's own route to a fingerprint is `mex graph scope --fingerprint`, which attaches
one to EVERY node it returns. A single fingerprint serializes to 1.7-16kB, so
grounding one claim that way costs an agent more context than reading the function
it is grounding. The graph already stores the fields, and the wire format is just
their JSON in hex, so this reads the one node asked for. Stdlib only.

    python3 tools/mex_grounds_to.py <node-id>   # after `mex graph`
"""

from __future__ import annotations

import json
import sqlite3
import struct
import sys
from contextlib import closing
from pathlib import Path

_GRAPH = Path(".mex/graph.db")
_ARGC = 2  # script name plus the node id


def _say(message: str) -> None:
    sys.stdout.write(f"{message}\n")


def _fail(message: str) -> None:
    sys.stderr.write(f"{message}\n")


def entry(node_id: str) -> int:
    """Print the entry for one node, ready to paste under `grounds_to:`."""
    if not _GRAPH.exists():
        _fail(f"{_GRAPH} missing — run `mex graph` first")
        return 1
    with closing(sqlite3.connect(_GRAPH)) as connection:
        row = connection.execute(
            "SELECT f.minhash, f.neighbors, f.token_count, n.body_hash "
            "FROM node_fingerprints f JOIN nodes n ON n.id = f.node_id WHERE f.node_id = ?",
            (node_id,),
        ).fetchone()
    if row is None or not row[3]:
        _fail(f"no fingerprint/body_hash for {node_id} — check the id against `mex graph query`")
        return 1
    # The graph stores the minhash as big-endian uint32s; the wire format wants the list.
    minhash = list(struct.unpack(f">{len(row[0]) // 4}I", row[0]))
    payload = {
        "minhash": minhash,
        "neighbors": json.loads(row[1]),
        "tokenCount": row[2],
    }
    serialized = json.dumps(payload, separators=(",", ":")).encode().hex()
    _say(f'  - node: "{node_id}"\n    fingerprint: "mh:64:{serialized}"\n    bodyHash: "{row[3]}"')
    return 0


def main() -> int:
    if len(sys.argv) != _ARGC:
        _fail("usage: mex_grounds_to.py <node-id>")
        return 2
    return entry(sys.argv[1])


if __name__ == "__main__":
    sys.exit(main())
