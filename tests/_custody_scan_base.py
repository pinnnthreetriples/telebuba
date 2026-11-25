"""Shared parts of the custody scans: the repo root, the manifest, and the parsed tree.

``production_files`` walks the top-level ``*.py`` files and every top-level package directory
that holds Python (``api/ core/ services/ schemas/ tools/`` today, and whatever is added
tomorrow), skipping tests, the frontend, docs, virtual environments and build or cache folders.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import tomllib
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "configs" / "custody_manifest.toml"
_SKIP_DIRS = {
    "tests",
    "frontend",
    "docs",
    "venv",
    "node_modules",
    "build",
    "dist",
    "htmlcov",
    "sessions",
    "site-packages",
    "__pycache__",
}


def load_gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "custody_gate", ROOT / "tools" / "custody_gate.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def manifest_paths() -> list[str]:
    return list(tomllib.loads(MANIFEST.read_text(encoding="utf-8"))["paths"])


def production_files(root: Path) -> list[Path]:
    """Top-level ``*.py`` (not ``conftest.py``) and ``*.py`` under every top-level package dir."""
    found = [p for p in sorted(root.glob("*.py")) if p.name != "conftest.py"]
    for top in sorted(root.iterdir()):
        if not top.is_dir() or top.name.startswith(".") or top.name in _SKIP_DIRS:
            continue
        for folder, dirs, files in os.walk(top):
            dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS and not d.startswith("."))
            found.extend(Path(folder, name) for name in sorted(files) if name.endswith(".py"))
    return found


@cache
def parsed(root: Path) -> dict[str, ast.Module]:
    return {
        path.relative_to(root).as_posix(): ast.parse(path.read_text(encoding="utf-8"))
        for path in production_files(root)
    }


def repo_files(root: Path) -> set[str]:
    """Every tracked-looking file of the repo, as repo-relative posix paths."""
    skip = {".git", ".venv", "node_modules", "__pycache__"}
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and not skip & set(path.relative_to(root).parts)
    }


def tail(node: ast.expr) -> str | None:
    """The last name of ``a.b.c`` (``c``) or a bare name; ``None`` for anything else."""
    if isinstance(node, ast.Name):
        return node.id
    return node.attr if isinstance(node, ast.Attribute) else None
