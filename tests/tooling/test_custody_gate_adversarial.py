"""Adversarial probes of ``tools/custody_gate.py`` against a real throw-away git repository.

Each test builds ``main`` (manifest, custody files, pyproject, lock), commits a PR on a
branch, and runs the script exactly as the workflow does (``--base main``, labels in the
``PR_LABELS`` environment variable, ``--head`` as the workflow passes it). Each test is a
claim about the gate that held or a bypass that has been closed.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Sequence

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "tools" / "custody_gate.py"
_SPEC = importlib.util.spec_from_file_location("custody_gate_adv", _SCRIPT)
assert _SPEC is not None
assert _SPEC.loader is not None
gate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gate)

_MANIFEST = (
    'label = "custody-reviewed"\n'
    "paths = [\n"
    '    "configs/custody_manifest.toml",\n'
    '    "tools/custody_gate.py",\n'
    '    ".github/workflows/custody.yml",\n'
    '    "core/session_file.py",\n'
    '    "services/session_guard/**",\n'
    "]\n"
)
_PYPROJECT = '[project]\nname = "x"\ndependencies = ["telethon>=1.45.0,<1.46", "httpx>=0.28"]\n'
_LOCK = (
    '[[package]]\nname = "httpx"\nversion = "0.28.1"\n\n'
    '[[package]]\nname = "telethon"\nversion = "1.45.0"\n'
    '[package.source]\nregistry = "https://pypi.org/simple"\n'
)
_FLAGS = ["-c", "user.email=t@example.invalid", "-c", "user.name=t", "-c", "commit.gpgsign=false"]


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(  # noqa: S603
        ["git", *_FLAGS, *args],  # noqa: S607
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
    )
    return done.stdout


def _write(repo: Path, name: str, text: str) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "r"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "core.autocrlf", "false")
    (root / "tools").mkdir()
    shutil.copy(_SCRIPT, root / "tools" / "custody_gate.py")
    _write(root, "configs/custody_manifest.toml", _MANIFEST)
    _write(root, ".github/workflows/custody.yml", "name: x\n")
    _write(root, "core/session_file.py", "x = 1\n" * 30)
    _write(root, "services/session_guard/a.py", "a = 1\n")
    _write(root, "docs/readme.md", "hi\n")
    _write(root, "pyproject.toml", _PYPROJECT)
    _write(root, "uv.lock", _LOCK)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "checkout", "-q", "-b", "pr")
    return root


def _commit(repo: Path) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "pr", "--allow-empty")


def _run(repo: Path, labels: str | Sequence[str] = ()) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "PR_LABELS"}
    env["PR_LABELS"] = labels if isinstance(labels, str) else json.dumps(list(labels))
    return subprocess.run(
        [sys.executable, "tools/custody_gate.py", "--base", "main", "--head", "pr"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )


def test_control_untouched_custody_passes_and_touched_fails(repo: Path) -> None:
    _write(repo, "docs/readme.md", "changed\n")
    _commit(repo)
    assert _run(repo).returncode == 0
    _write(repo, "core/session_file.py", "y = 2\n")
    _commit(repo)
    assert _run(repo).returncode == 1
    assert _run(repo, ["custody-reviewed"]).returncode == 0


@pytest.mark.parametrize(
    "labels",
    [
        ["Custody-Reviewed"],
        [" custody-reviewed"],
        ["custody-reviewed "],
        ["custody_reviewed"],
        "custody-reviewed",
        '"custody-reviewed"',
        '{"0": "custody-reviewed"}',
        "not json",
        "",
        "null",
        "[1, null]",
    ],
)
def test_a_label_that_is_not_exactly_the_label_does_not_pass(
    repo: Path, labels: str | list[str]
) -> None:
    _write(repo, "core/session_file.py", "y = 2\n")
    _commit(repo)
    assert _run(repo, labels).returncode == 1


def test_a_deleted_manifest_never_passes(repo: Path) -> None:
    (repo / "configs" / "custody_manifest.toml").unlink()
    _write(repo, "core/session_file.py", "y = 2\n")
    _commit(repo)
    assert _run(repo).returncode != 0


def test_an_unparsable_manifest_never_passes(repo: Path) -> None:
    _write(repo, "configs/custody_manifest.toml", "paths = [[[\n")
    _write(repo, "core/session_file.py", "y = 2\n")
    _commit(repo)
    assert _run(repo).returncode != 0


def test_a_mode_only_change_of_a_custody_file_is_seen(repo: Path) -> None:
    _git(repo, "update-index", "--chmod=+x", "core/session_file.py")
    # Commit the index as it is: `git add -A` would take the mode back from the working
    # tree wherever git tracks file modes (Linux), and the PR would change nothing.
    _git(repo, "commit", "-q", "-m", "pr")
    assert _run(repo).returncode == 1


def test_a_path_with_odd_characters_is_matched_literally(repo: Path) -> None:
    _write(repo, "services/session_guard/sp ace/éé q.py", "z = 1\n")
    _commit(repo)
    assert _run(repo).returncode == 1


def test_a_custody_file_deleted_is_seen(repo: Path) -> None:
    (repo / "core" / "session_file.py").unlink()
    _commit(repo)
    assert _run(repo).returncode == 1


def test_a_telethon_bound_change_in_pyproject_is_seen(repo: Path) -> None:
    _write(repo, "pyproject.toml", _PYPROJECT.replace("<1.46", "<1.47"))
    _commit(repo)
    assert _run(repo).returncode == 1


@pytest.mark.parametrize(
    "requirement",
    ["Telethon [cryptg] >=1.45.0,<1.46", "TELETHON>=1.45.0,<1.47", "telethon @ git+https://x/y"],
)
def test_a_reformatted_telethon_requirement_is_seen(repo: Path, requirement: str) -> None:
    _write(
        repo,
        "pyproject.toml",
        f'[project]\nname = "x"\ndependencies = ["{requirement}", "httpx>=0.28"]\n',
    )
    _commit(repo)
    assert _run(repo).returncode == 1


def test_a_second_telethon_lock_entry_is_seen(repo: Path) -> None:
    _write(repo, "uv.lock", _LOCK + '\n[[package]]\nname = "telethon"\nversion = "1.45.1"\n')
    _commit(repo)
    assert _run(repo).returncode == 1


def test_telethon_lock_bump_is_seen(repo: Path) -> None:
    _write(repo, "uv.lock", _LOCK.replace('"1.45.0"', '"1.45.3"'))
    _commit(repo)
    assert _run(repo).returncode == 1


def test_an_unrelated_dependency_bump_does_not_trip_the_gate(repo: Path) -> None:
    _write(repo, "uv.lock", _LOCK.replace("0.28.1", "0.28.2"))
    _write(repo, "pyproject.toml", _PYPROJECT.replace("httpx>=0.28", "httpx>=0.28.1"))
    _commit(repo)
    assert _run(repo).returncode == 0


def test_telethon_source_swap_with_same_version_is_seen(repo: Path) -> None:
    swapped = _LOCK.replace(
        'registry = "https://pypi.org/simple"', 'git = "https://example.invalid/telethon"'
    )
    _write(repo, "uv.lock", swapped)
    _commit(repo)
    assert _run(repo).returncode == 1


def test_a_pr_cannot_exempt_itself_by_shortening_the_manifest(repo: Path) -> None:
    _write(repo, "configs/custody_manifest.toml", 'label = "custody-reviewed"\npaths = []\n')
    _write(repo, "core/session_file.py", "y = 2\n")
    _commit(repo)
    assert _run(repo).returncode == 1


def test_the_gates_own_files_are_custody_even_if_the_pr_manifest_omits_them(repo: Path) -> None:
    _write(repo, "configs/custody_manifest.toml", 'label = "custody-reviewed"\npaths = ["x"]\n')
    _commit(repo)
    # the manifest edit itself is the first thing that must trip
    assert _run(repo).returncode == 1


def test_moving_a_custody_file_out_of_custody_is_seen(repo: Path) -> None:
    _git(repo, "mv", "core/session_file.py", "docs/session_file_moved.py")
    _commit(repo)
    assert _run(repo).returncode == 1


def test_gate_patterns_unioned_function_is_correct_in_isolation() -> None:
    # The helper that would close the self-exemption is right; only main() ignores it.
    patterns, label = gate.gate_patterns(_MANIFEST, 'label = "x"\npaths = []\n')
    assert "core/session_file.py" in patterns
    assert "tools/custody_gate.py" in patterns
    assert label == "custody-reviewed"
    patterns, _ = gate.gate_patterns(_MANIFEST, "paths = [[[")
    assert "core/session_file.py" in patterns
    patterns, _ = gate.gate_patterns("", "")
    assert "configs/custody_manifest.toml" in patterns


def test_a_glob_does_not_leak_across_segments_or_prefixes() -> None:
    assert gate.custody_paths(["core/session_file.py.bak"], ["core/session_file.py"]) == []
    assert (
        gate.custody_paths(["services/session_guardian/x.py"], ["services/session_guard/**"]) == []
    )
    assert gate.custody_paths(["core/session_file.py"], ["core\\session_file.py"]) == []
    # a backslash path from a Windows checkout is never custody: git prints forward slashes
