"""Deletion ratchet scan: calls that remove or overwrite a file, counted per file.

Part of the custody scans (see ``tests/_custody_scan.py``). Counted:
``os.remove/unlink/removedirs/replace/rename``, ``shutil.rmtree/move`` (through ``import ... as``
and ``from ... import`` aliases), ``send2trash``, ``.unlink()``/``.rmdir()`` on anything,
``.replace(x)``/``.rename(x)`` with ONE argument on a receiver that is plausibly a path (a
heuristic, see ``_pathy``), and ``X.session.delete()`` (Telethon's ``SQLiteSession.delete``
removes the session file).
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from tests._custody_scan_base import load_gate, manifest_paths, parsed, tail

if TYPE_CHECKING:
    from pathlib import Path

_MODULE_CALLS = {
    "os.remove",
    "os.unlink",
    "os.removedirs",
    "os.replace",
    "os.rename",
    "shutil.rmtree",
    "shutil.move",
    "send2trash.send2trash",
}
_ANY_RECEIVER = {"unlink", "rmdir"}
_PATH_CLASSES = {"Path", "AsyncPath", "PurePath"}
_PATH_METHODS = {"with_suffix", "with_name", "with_stem", "joinpath", "resolve", "absolute"}
# Heuristic: a name (or attribute) ending in one of these is taken to hold a path.
_PATHY_SUFFIXES = (
    "path",
    "file",
    "dir",
    "target",
    "source",
    "aside",
    "scratch",
    "final",
    "tmp",
    "staged",
)


def _aliases(tree: ast.Module) -> dict[str, str]:
    """Local name -> dotted origin, for ``import os as o`` and ``from os import replace as r``."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update({alias.asname or alias.name: alias.name for alias in node.names})
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.update(
                {alias.asname or alias.name: f"{node.module}.{alias.name}" for alias in node.names}
            )
    return out


def _dotted(func: ast.expr, aliases: dict[str, str]) -> str | None:
    if isinstance(func, ast.Name):
        return aliases.get(func.id)
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return f"{aliases.get(func.value.id, func.value.id)}.{func.attr}"
    return None


def _path_names(tree: ast.Module) -> set[str]:
    """Names annotated as a path class or assigned from ``Path(...)``."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.arg | ast.AnnAssign) and node.annotation is not None:
            if any(klass in ast.unparse(node.annotation) for klass in _PATH_CLASSES):
                target = node.arg if isinstance(node, ast.arg) else ast.unparse(node.target)
                names.add(target)
        elif isinstance(node, ast.Assign) and _pathy(node.value, set()):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def _pathy(node: ast.expr, names: set[str]) -> bool:
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name | ast.Attribute) and tail(func) in _PATH_CLASSES:
            return True
        return (
            isinstance(func, ast.Attribute)
            and func.attr in _PATH_METHODS
            and _pathy(func.value, names)
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _pathy(node.left, names)
    if isinstance(node, ast.Attribute) and node.attr == "parent":
        return _pathy(node.value, names)
    label = tail(node)
    return label is not None and (
        label in names or label.lower().strip("_").endswith(_PATHY_SUFFIXES)
    )


def _destructive_calls(tree: ast.Module) -> int:
    aliases = _aliases(tree)
    paths = _path_names(tree)
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        dotted = _dotted(func, aliases)
        if dotted in _MODULE_CALLS:
            count += 1
            continue
        if not isinstance(func, ast.Attribute):
            continue
        one_arg = len(node.args) == 1 and not node.keywords
        on_session = (
            func.attr == "delete"
            and not node.args
            and isinstance(func.value, ast.Attribute)
            and func.value.attr == "session"
        )
        count += (
            func.attr in _ANY_RECEIVER
            or (func.attr in {"replace", "rename"} and one_arg and _pathy(func.value, paths))
            or on_session
        )
    return count


def in_deletion_scope(rel: str, scope: tuple[str, ...], patterns: list[str]) -> bool:
    """A scope prefix (or file) or any file the custody manifest matches."""
    return rel.startswith(scope) or load_gate().matches_any(rel, patterns)


def destructive_counts(root: Path, scope: tuple[str, ...]) -> dict[str, int]:
    """File -> destructive calls, for files under ``scope`` or matched by the manifest."""
    patterns = manifest_paths()
    counts = {
        rel: _destructive_calls(tree)
        for rel, tree in parsed(root).items()
        if in_deletion_scope(rel, scope, patterns)
    }
    return {rel: count for rel, count in counts.items() if count}
