"""Tests for the custody merge gate (``tools/custody_gate.py``)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "custody_gate", Path(__file__).resolve().parents[2] / "tools" / "custody_gate.py"
)
assert _SPEC is not None
assert _SPEC.loader is not None
gate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gate)

_LABEL = "custody-reviewed"
_PYPROJECT = (
    '[project]\nname = "x"\ndependencies = [\n    "telethon>=1.45.0",\n    "httpx>=0.28",\n]\n'
)
_LOCK = (
    '[[package]]\nname = "httpx"\nversion = "0.28.1"\n\n'
    '[[package]]\nname = "telethon"\nversion = "1.45.0"\n'
)


@pytest.mark.parametrize(
    ("path", "pattern"),
    [
        ("services/session_guard/_loop.py", "services/session_guard/**"),
        ("services/session_guard/deep/x.py", "services/session_guard/**"),
        ("core/telegram_client/_rekey_token.py", "core/telegram_client/_rekey*.py"),
        ("core/a/b/c.py", "core/**/c.py"),
        ("core/c.py", "core/**/c.py"),
        ("core/db.py", "core/db.py"),
        ("core/dbx.py", "core/db?.py"),
    ],
)
def test_glob_matches(path: str, pattern: str) -> None:
    assert gate.custody_paths([path], [pattern]) == [path]


@pytest.mark.parametrize(
    ("path", "pattern"),
    [
        ("services/session_guard", "services/session_guard/**"),
        ("core/telegram_client/sub/_rekey.py", "core/telegram_client/_rekey*.py"),
        ("core/db_x.py", "core/db.py"),
        ("core/db/x.py", "core/db?.py"),
    ],
)
def test_glob_does_not_match(path: str, pattern: str) -> None:
    assert gate.custody_paths([path], [pattern]) == []


def test_custody_paths_keeps_input_order() -> None:
    changed = ["b/z.py", "a/y.py", "a/x.py"]
    assert gate.custody_paths(changed, ["a/*.py", "b/*.py"]) == changed


def test_telethon_requirement_bump_counts() -> None:
    bumped = _PYPROJECT.replace("telethon>=1.45.0", "telethon>=1.46.0")
    assert gate.telethon_changed(_PYPROJECT, bumped, _LOCK, _LOCK)


def test_telethon_locked_version_bump_counts() -> None:
    bumped = _LOCK.replace('version = "1.45.0"', 'version = "1.45.1"')
    assert gate.telethon_changed(_PYPROJECT, _PYPROJECT, _LOCK, bumped)


def test_unrelated_bump_does_not_count() -> None:
    pyproject = _PYPROJECT.replace("httpx>=0.28", "httpx>=0.29")
    lock = _LOCK.replace('version = "0.28.1"', 'version = "0.29.0"')
    assert not gate.telethon_changed(_PYPROJECT, pyproject, _LOCK, lock)


def test_a_package_named_like_telethon_does_not_count() -> None:
    pyproject = _PYPROJECT.replace("httpx>=0.28", "telethon-extras>=1")
    assert not gate.telethon_changed(_PYPROJECT, pyproject, _LOCK, _LOCK)


def test_missing_base_files_count_as_a_change() -> None:
    assert gate.telethon_changed("", _PYPROJECT, "", _LOCK)


@pytest.mark.parametrize(
    ("raw", "labels"),
    [
        (None, []),
        ("", []),
        ("  ", []),
        ("[]", []),
        ('["a", "custody-reviewed"]', ["a", "custody-reviewed"]),
        ("not json", []),
        ('{"custody-reviewed": true}', []),
        ('"custody-reviewed"', []),
        ('["a", 3, null]', ["a"]),
    ],
)
def test_parse_labels(raw: str | None, labels: list[str]) -> None:
    assert gate.parse_labels(raw) == labels


def test_no_custody_change_passes() -> None:
    code, text = gate.decide([], [], _LABEL, telethon_bumped=False)
    assert code == 0
    assert "\n" not in text


def test_custody_change_with_label_passes() -> None:
    code, text = gate.decide(
        ["core/session_vault.py"], ["x", _LABEL], _LABEL, telethon_bumped=False
    )
    assert code == 0
    assert "\n" not in text


def test_custody_change_without_label_fails_and_lists_files() -> None:
    code, text = gate.decide(["core/session_vault.py"], ["other"], _LABEL, telethon_bumped=False)
    assert code == 1
    assert "core/session_vault.py" in text
    assert text.splitlines()[-1].startswith("The repository owner reviews")
    assert "agents must not add it" in text


def test_telethon_bump_without_label_fails() -> None:
    code, text = gate.decide([], [], _LABEL, telethon_bumped=True)
    assert code == 1
    assert "Telethon" in text


def test_the_file_list_is_capped() -> None:
    files = [f"core/f{i}.py" for i in range(100)]
    code, text = gate.decide(files, [], _LABEL, telethon_bumped=False)
    assert code == 1
    assert len(text.splitlines()) <= 30
    assert "more" in text


def test_a_pr_cannot_take_an_entry_off_the_list_it_is_judged_by() -> None:
    base = 'label = "custody-reviewed"\npaths = ["core/session_vault.py"]\n'
    head = 'label = "anything"\npaths = ["docs/**"]\n'
    patterns, label = gate.gate_patterns(base, head)
    assert "core/session_vault.py" in patterns
    assert "docs/**" in patterns
    assert label == "custody-reviewed"


def test_the_gate_guards_its_own_parts_without_a_manifest() -> None:
    patterns, label = gate.gate_patterns("", "not = [toml")
    assert gate.custody_paths(
        ["tools/custody_gate.py", ".github/workflows/custody.yml", "README.md"], patterns
    ) == ["tools/custody_gate.py", ".github/workflows/custody.yml"]
    assert label == "custody-reviewed"


def test_the_head_manifest_names_the_label_when_the_base_has_none() -> None:
    _, label = gate.gate_patterns("", 'label = "owner-ok"\npaths = []\n')
    assert label == "owner-ok"
