"""SPA catch-all path-traversal containment (finding #1).

The route helper ``_safe_spa_file`` decides whether a requested path resolves
to a real file *inside* ``frontend/dist``. Starlette's TestClient normalizes
``..`` before the app sees it, so the containment decision is unit-tested on
the pure helper directly with traversal inputs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import main

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    "path",
    [
        "../../.env",  # parent traversal
        "..\\..\\.env",  # backslash traversal (Windows separators)
        "/etc/passwd",  # absolute (posix)
        "C:\\Windows\\win.ini",  # absolute (windows)
        "assets/../../telebuba.db",  # dotdot segment mid-path
    ],
)
def test_safe_spa_file_rejects_traversal_and_absolute_paths(path: str) -> None:
    assert main._safe_spa_file(path) is None


def test_safe_spa_file_resolves_a_normal_asset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    asset = dist / "assets" / "x.js"
    asset.write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setattr(main, "_FRONTEND_DIST", dist)
    resolved = main._safe_spa_file("assets/x.js")
    assert resolved == asset.resolve()


def test_safe_spa_file_returns_none_for_missing_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "_FRONTEND_DIST", tmp_path / "dist")
    assert main._safe_spa_file("assets/nope.js") is None
