"""Tests for the Claude Code Stop hook that asks for MEX GROW once."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "mex_grow_gate.py"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed argv in a throwaway repo
        ["git", "-c", "commit.gpgsign=false", *args],  # noqa: S607 - git from PATH, as the hook runs it
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _commit(repo: Path, path: str, content: str = "x = 1\n") -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(repo, "add", "--", path)
    _git(repo, "commit", "--no-verify", "-q", "-m", f"touch {path}")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for key in ("GIT_AUTHOR", "GIT_COMMITTER"):
        monkeypatch.setenv(f"{key}_NAME", "test")
        monkeypatch.setenv(f"{key}_EMAIL", "test@example.invalid")
    _git(tmp_path, "init", "-q", "-b", "main")
    _commit(tmp_path, "README.md", "base\n")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(tmp_path, "switch", "-q", "-c", "feature")
    return tmp_path


def _stop(cwd: Path, *, session: str = "s1", active: bool = False, raw: str | None = None) -> str:
    payload = {"session_id": session, "stop_hook_active": active, "cwd": str(cwd)}
    completed = subprocess.run(  # noqa: S603 - this interpreter running the repo script
        [sys.executable, str(_SCRIPT)],
        input=json.dumps(payload) if raw is None else raw,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert completed.stderr == ""
    return completed.stdout


def _blocks(output: str) -> bool:
    if not output:
        return False
    decision = json.loads(output)
    assert decision["reason"].startswith("MEX GROW")
    return decision["decision"] == "block"


def test_blocks_code_commit_without_memory(repo: Path) -> None:
    _commit(repo, "services/sample.py")

    assert _blocks(_stop(repo))


def test_blocks_from_subdirectory(repo: Path) -> None:
    _commit(repo, "frontend/src/app.ts")

    assert _blocks(_stop(repo / "frontend"))


def test_prompts_once_per_session_and_head(repo: Path) -> None:
    _commit(repo, "api/route.py")

    assert _blocks(_stop(repo))
    assert _stop(repo) == ""
    assert _blocks(_stop(repo, session="s2"))

    _commit(repo, "core/gateway.py")
    assert _blocks(_stop(repo))


def test_marker_lives_inside_git_dir(repo: Path) -> None:
    _commit(repo, "main.py")
    _stop(repo, session="a/b")

    head = _git(repo, "rev-parse", "HEAD").strip()
    assert (repo / ".git" / "mex-grow-prompted" / f"a_b.{head}").exists()
    assert _git(repo, "status", "--porcelain") == ""


def test_stop_hook_active_never_blocks(repo: Path) -> None:
    _commit(repo, "schemas/model.py")

    assert _stop(repo, active=True) == ""
    assert _blocks(_stop(repo))


def test_memory_in_commits_passes(repo: Path) -> None:
    _commit(repo, "tools/helper.py")
    _commit(repo, ".mex/context/setup.md", "note\n")

    assert _stop(repo) == ""


def test_memory_in_working_tree_passes(repo: Path) -> None:
    _commit(repo, "services/sample.py")
    (repo / ".mex").mkdir()
    (repo / ".mex" / "draft.md").write_text("draft\n", encoding="utf-8")

    assert _stop(repo) == ""


def test_docs_only_change_passes(repo: Path) -> None:
    _commit(repo, "docs/guide.md", "guide\n")
    _commit(repo, "frontend/package.json", "{}\n")

    assert _stop(repo) == ""


def test_no_commits_ahead_passes(repo: Path) -> None:
    assert _stop(repo) == ""


def test_uncommitted_code_passes(repo: Path) -> None:
    (repo / "api").mkdir()
    (repo / "api" / "wip.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "--", "api/wip.py")

    assert _stop(repo) == ""


def test_missing_origin_main_fails_open(repo: Path) -> None:
    _commit(repo, "api/route.py")
    _git(repo, "update-ref", "-d", "refs/remotes/origin/main")

    assert _stop(repo) == ""


def test_not_a_repository_fails_open(tmp_path: Path) -> None:
    assert _stop(tmp_path) == ""


@pytest.mark.parametrize("raw", ["", "not json", "[]", '{"stop_hook_active": false}'])
def test_unusable_input_fails_open(repo: Path, raw: str) -> None:
    _commit(repo, "api/route.py")

    assert _stop(repo, raw=raw) == ""
