"""AST scans behind ``tests/test_custody_invariants.py``.

Every scan takes a ``root`` (the repo, or a ``tmp_path`` holding a planted violation) and walks
the top-level ``*.py`` files and every top-level package directory with Python in it (tests,
frontend, docs, venvs and build folders excluded), so a self-test runs the same code the real
tree does. Source files are parsed once per root. The deletion and 2FA-write scans live in
``tests/_custody_scan_fs.py`` and ``tests/_custody_scan_write.py``; this module re-exports them.

What the scans do not see (documented limitations, each has a non-strict ``xfail`` probe in
``tests/test_custody_invariants_adversarial.py``): a request or client named by a string
(``getattr(functions.auth, "Reset" + ...)``, ``__dict__[...]``, ``importlib``); SQL assembled so
that a column name never sits in one string constant; ``functools.partial`` and
``operator.methodcaller`` indirection; a file removed through ``subprocess`` (``rm``, ``del``) or
emptied by ``write_bytes(b"")``; reach-through to a private module without an import statement.
Receiver detection for ``.replace()``/``.rename()`` is a heuristic (see ``_custody_scan_fs``).
"""

from __future__ import annotations

import ast
from collections import defaultdict
from typing import TYPE_CHECKING

from tests._custody_scan_base import (
    MANIFEST,
    ROOT,
    load_gate,
    manifest_paths,
    parsed,
    repo_files,
    tail,
)
from tests._custody_scan_fs import destructive_counts
from tests._custody_scan_write import column_writers

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "MANIFEST",
    "ROOT",
    "client_constructions",
    "column_writers",
    "destructive_counts",
    "manifest_misses",
    "manifest_paths",
    "private_imports",
    "repo_files",
    "symbol_users",
    "uncovered_by_manifest",
]


def uncovered_by_manifest(files: set[str]) -> list[str]:
    """Files no manifest entry matches (the gate's own glob rules)."""
    matches_any = load_gate().matches_any
    patterns = manifest_paths()
    return sorted(name for name in files if not matches_any(name, patterns))


def manifest_misses(patterns: list[str], files: set[str]) -> list[str]:
    """Manifest entries that match no existing file."""
    matches_any = load_gate().matches_any
    return [
        pattern for pattern in patterns if not any(matches_any(name, [pattern]) for name in files)
    ]


def client_classes(root: Path) -> set[str]:
    """``TelegramClient``, every class the repo derives from it, and every name bound to one.

    Follows ``from telethon import TelegramClient as TC`` and a plain assignment alias
    (``factory = TelegramClient``), however deep.
    """
    found = {"TelegramClient"}
    nodes = [node for tree in parsed(root).values() for node in ast.walk(tree)]
    grew = True
    while grew:
        grew = False
        for node in nodes:
            bound: list[str] = []
            if isinstance(node, ast.ClassDef) and any(tail(base) in found for base in node.bases):
                bound = [node.name]
            elif isinstance(node, ast.ImportFrom):
                bound = [a.asname for a in node.names if a.asname and a.name in found]
            elif isinstance(node, ast.Assign | ast.AnnAssign) and node.value is not None:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if tail(node.value) in found:
                    bound = [t.id for t in targets if isinstance(t, ast.Name)]
            fresh = [name for name in bound if name not in found]
            found.update(fresh)
            grew = grew or bool(fresh)
    return found


def client_constructions(root: Path) -> dict[str, int]:
    """File -> how many times it instantiates a Telethon client class."""
    names = client_classes(root)
    out: dict[str, int] = defaultdict(int)
    for rel, tree in parsed(root).items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and tail(node.func) in names:
                out[rel] += 1
    return dict(out)


def symbol_users(root: Path, names: set[str], methods: set[str]) -> dict[str, set[str]]:
    """Name -> files that import or reference it.

    ``names`` match a bare name, an attribute or an imported name (a request class). ``methods``
    match attribute access only (``client.log_out``), so an unrelated function of the same
    name is not a hit.
    """
    out: dict[str, set[str]] = defaultdict(set)
    for rel, tree in parsed(root).items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in names | methods:
                out[node.attr].add(rel)
            elif isinstance(node, ast.Name) and node.id in names:
                out[node.id].add(rel)
            elif isinstance(node, ast.alias) and node.name.split(".")[-1] in names:
                out[node.name.split(".")[-1]].add(rel)
    return dict(out)


def _import_targets(node: ast.stmt, package: str) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if not isinstance(node, ast.ImportFrom):
        return []
    parts = package.split(".") if node.level else []
    parts = parts[: max(len(parts) - (node.level - 1), 0)] if node.level else []
    module = ".".join([*parts, *([node.module] if node.module else [])])
    return [f"{module}.{alias.name}" for alias in node.names]


def private_imports(root: Path, package: str) -> dict[str, set[str]]:
    """File -> private ``package._x`` symbols it imports, for files outside ``package``.

    A symbol is the dotted name an import binds (``services.accounts._result.result_code``,
    or ``core.telegram_client._chats`` for a module imported whole).
    """
    home = package.replace(".", "/") + "/"
    prefix = package + "._"
    out: dict[str, set[str]] = defaultdict(set)
    for rel, tree in parsed(root).items():
        if rel.startswith(home):
            continue
        pkg = ".".join(rel.split("/")[:-1])
        for node in ast.walk(tree):
            if isinstance(node, ast.Import | ast.ImportFrom):
                out[rel].update(s for s in _import_targets(node, pkg) if s.startswith(prefix))
    return {rel: symbols for rel, symbols in out.items() if symbols}
