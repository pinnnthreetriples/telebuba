"""``tools/mex_grounds_to.py`` — the `grounds_to` entry for a new grounded claim.

Both failure modes produce frontmatter that looks plausible but leaves the gate
unenforced: a fingerprint mex cannot deserialize, or an entry pasted for a node
the graph does not know.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import struct
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

_SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "mex_grounds_to.py"
_NODE = "function:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_OTHER = "function:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _load() -> ModuleType:
    """Import the script by path — ``tools/`` is deliberately not a package (INP001)."""
    spec = importlib.util.spec_from_file_location("mex_grounds_to", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _graph(tmp_path: Path, nodes: dict[str, str]) -> None:
    """A stand-in `.mex/graph.db` carrying only what the script reads."""
    connection = sqlite3.connect(tmp_path / ".mex" / "graph.db")
    with connection:
        connection.execute("CREATE TABLE nodes (id TEXT PRIMARY KEY, body_hash TEXT)")
        connection.execute(
            "CREATE TABLE node_fingerprints ("
            "node_id TEXT PRIMARY KEY, minhash BLOB NOT NULL, "
            "neighbors TEXT NOT NULL, token_count INTEGER NOT NULL)",
        )
        connection.executemany("INSERT INTO nodes VALUES (?, ?)", list(nodes.items()))
    connection.close()


@pytest.fixture
def grounds_to(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".mex").mkdir()
    return _load()


def test_entry_serializes_the_way_mex_does(
    grounds_to: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The wire format is hex of the fingerprint's JSON, with mex's own key order.

    Get either wrong and the frontmatter looks plausible but never deserializes, so
    `GROUNDING_GONE` can no longer reconcile a moved symbol and reports it as deleted.
    The body hash rides along: without it mex has no baseline and skips drift.
    """
    _graph(tmp_path, {_NODE: "hash-one"})
    connection = sqlite3.connect(tmp_path / ".mex" / "graph.db")
    with connection:
        connection.execute(
            "INSERT INTO node_fingerprints VALUES (?, ?, ?, ?)",
            (_NODE, struct.pack(">2I", 1, 2), f'["{_OTHER}"]', 7),
        )
    connection.close()

    assert grounds_to.entry(_NODE) == 0

    node_line, fingerprint_line, hash_line = capsys.readouterr().out.splitlines()
    assert node_line == f'  - node: "{_NODE}"'
    payload = bytes.fromhex(fingerprint_line.split("mh:64:")[1].rstrip('"'))
    assert payload == f'{{"minhash":[1,2],"neighbors":["{_OTHER}"],"tokenCount":7}}'.encode()
    assert hash_line == '    bodyHash: "hash-one"'


def test_entry_refuses_a_node_the_graph_does_not_know(
    grounds_to: ModuleType, tmp_path: Path
) -> None:
    """A wrong id must not yield frontmatter — pasting one grounds a claim to nothing."""
    _graph(tmp_path, {_NODE: "hash-one"})

    assert grounds_to.entry(_NODE) == 1


def test_a_missing_graph_fails_loudly(grounds_to: ModuleType) -> None:
    assert grounds_to.entry(_NODE) == 1


def test_main_requires_exactly_one_node_id(
    grounds_to: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "argv", ["mex_grounds_to.py"])

    assert grounds_to.main() == 2
