"""``tools/mex_grounding_baseline.py`` — the tracked baseline behind GROUNDING_DRIFT.

Three failure modes are worth a test, because each one turns the gate green while
leaving it unenforced, which is worse than not having it: a baseline that does not
cover every grounded claim, a key written without the ``.mex/`` prefix mex looks it
up by, and a re-captured hash shipped with an untouched note.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

_SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "mex_grounding_baseline.py"
_NODE = "function:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_OTHER = "function:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
_NOTE = ".mex/context/runtime-neurocomment.md"


def _load() -> ModuleType:
    """Import the script by path — ``tools/`` is deliberately not a package (INP001)."""
    spec = importlib.util.spec_from_file_location("mex_grounding_baseline", _SCRIPT)
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
            "CREATE TABLE _mex_grounded_source ("
            "scaffold_file TEXT NOT NULL, node_id TEXT NOT NULL, source TEXT NOT NULL, "
            "body_hash TEXT NOT NULL, fingerprint TEXT NOT NULL, "
            "PRIMARY KEY (scaffold_file, node_id))",
        )
        connection.executemany("INSERT INTO nodes VALUES (?, ?)", list(nodes.items()))
    connection.close()


def _note(tmp_path: Path, *nodes: str) -> None:
    grounds = "\n".join(
        f'  - node: "{node}"\n    fingerprint: "mh:64:{index}"' for index, node in enumerate(nodes)
    )
    body = f"---\nlast_updated: 2026-08-07\ngrounds_to:\n{grounds}\n---\n\n# Note\n\n- a claim\n"
    (tmp_path / _NOTE).write_text(body, encoding="utf-8")


@pytest.fixture
def baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".mex" / "context").mkdir(parents=True)
    return _load()


def test_capture_records_the_current_hash_per_grounded_node(
    baseline: ModuleType, tmp_path: Path
) -> None:
    _graph(tmp_path, {_NODE: "hash-one", _OTHER: "hash-two"})
    _note(tmp_path, _NODE, _OTHER)

    assert baseline.capture() == 0

    rows = json.loads((tmp_path / ".mex" / "grounding-baseline.json").read_text(encoding="utf-8"))
    assert {row["node_id"]: row["body_hash"] for row in rows} == {
        _NODE: "hash-one",
        _OTHER: "hash-two",
    }


def test_capture_keys_the_baseline_the_way_mex_looks_it_up(
    baseline: ModuleType, tmp_path: Path
) -> None:
    """`checkGrounding` keys by the path from the PROJECT root, so `.mex/` is part of it.

    Writing `context/…` instead costs nothing loudly — the lookup simply never
    matches and every drift goes unreported.
    """
    _graph(tmp_path, {_NODE: "hash-one"})
    _note(tmp_path, _NODE)

    assert baseline.capture() == 0

    rows = json.loads((tmp_path / ".mex" / "grounding-baseline.json").read_text(encoding="utf-8"))
    assert rows[0]["scaffold_file"] == _NOTE


def test_apply_writes_the_row_the_drift_check_reads(baseline: ModuleType, tmp_path: Path) -> None:
    _graph(tmp_path, {_NODE: "hash-one"})
    _note(tmp_path, _NODE)
    assert baseline.capture() == 0

    assert baseline.apply() == 0

    connection = sqlite3.connect(tmp_path / ".mex" / "graph.db")
    stored = connection.execute(
        "SELECT scaffold_file, node_id, body_hash FROM _mex_grounded_source"
    ).fetchall()
    connection.close()
    assert stored == [(_NOTE, _NODE, "hash-one")]


def test_apply_refuses_a_claim_grounded_without_capturing(
    baseline: ModuleType, tmp_path: Path
) -> None:
    """Otherwise the new claim is enforced for existence but never for drift."""
    _graph(tmp_path, {_NODE: "hash-one", _OTHER: "hash-two"})
    _note(tmp_path, _NODE)
    assert baseline.capture() == 0
    _note(tmp_path, _NODE, _OTHER)  # grounded a second claim, forgot to re-capture

    assert baseline.apply() == 1


def test_apply_refuses_a_baseline_for_a_claim_no_longer_grounded(
    baseline: ModuleType, tmp_path: Path
) -> None:
    _graph(tmp_path, {_NODE: "hash-one", _OTHER: "hash-two"})
    _note(tmp_path, _NODE, _OTHER)
    assert baseline.capture() == 0
    _note(tmp_path, _NODE)  # dropped a grounding, forgot to re-capture

    assert baseline.apply() == 1


def test_verify_blocks_a_re_capture_that_left_the_note_alone(
    baseline: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hole this exists to close: refresh the hash, ship the stale sentence."""
    _graph(tmp_path, {_NODE: "hash-two"})
    _note(tmp_path, _NODE)
    assert baseline.capture() == 0
    previous = json.dumps([{"scaffold_file": _NOTE, "node_id": _NODE, "body_hash": "hash-one"}])

    def fake_git(*args: str) -> str | None:
        # The note is absent from the branch's changed-file list.
        return previous if args[0] == "show" else "services/neurocomment/_captcha_retry.py\n"

    monkeypatch.setattr(baseline, "_git", fake_git)

    assert baseline.verify("origin/main") == 1


def test_verify_passes_when_the_note_was_edited_too(
    baseline: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _graph(tmp_path, {_NODE: "hash-two"})
    _note(tmp_path, _NODE)
    assert baseline.capture() == 0
    previous = json.dumps([{"scaffold_file": _NOTE, "node_id": _NODE, "body_hash": "hash-one"}])

    monkeypatch.setattr(
        baseline,
        "_git",
        lambda *args: previous if args[0] == "show" else f"{_NOTE}\n",
    )

    assert baseline.verify("origin/main") == 0


def test_verify_ignores_a_newly_grounded_claim(
    baseline: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A first hash contradicts nothing, and its file is under review anyway."""
    _graph(tmp_path, {_NODE: "hash-one"})
    _note(tmp_path, _NODE)
    assert baseline.capture() == 0

    monkeypatch.setattr(baseline, "_git", lambda *args: "[]" if args[0] == "show" else "")

    assert baseline.verify("origin/main") == 0


def test_verify_is_a_no_op_before_the_baseline_exists_on_the_base(
    baseline: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The PR that introduces the baseline has nothing to compare against."""
    _graph(tmp_path, {_NODE: "hash-one"})
    _note(tmp_path, _NODE)
    assert baseline.capture() == 0

    monkeypatch.setattr(baseline, "_git", lambda *_args: None)

    assert baseline.verify("origin/main") == 0


def test_a_renamed_mex_table_fails_loudly(baseline: ModuleType, tmp_path: Path) -> None:
    """A silent skip here would leave the drift check disabled but green."""
    connection = sqlite3.connect(tmp_path / ".mex" / "graph.db")
    with connection:
        connection.execute("CREATE TABLE nodes (id TEXT PRIMARY KEY, body_hash TEXT)")
    connection.close()
    _note(tmp_path, _NODE)

    with pytest.raises(SystemExit):
        baseline.capture()


def test_a_missing_graph_fails_loudly(baseline: ModuleType, tmp_path: Path) -> None:
    _note(tmp_path, _NODE)

    with pytest.raises(SystemExit):
        baseline.capture()


def test_capture_refuses_a_grounding_the_graph_does_not_know(
    baseline: ModuleType, tmp_path: Path
) -> None:
    _graph(tmp_path, {_NODE: "hash-one"})
    _note(tmp_path, _NODE, _OTHER)

    assert baseline.capture() == 1


def test_main_rejects_an_unknown_subcommand(
    baseline: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "argv", ["mex_grounding_baseline.py", "nonsense"])

    assert baseline.main() == 2
