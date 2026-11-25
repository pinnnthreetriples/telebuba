"""2FA column write scan: who can write the stored 2FA password and its pending fields.

Part of the custody scans (see ``tests/_custody_scan.py``). A file is a writer when it has

* a ``values/update/insert/on_conflict_do_update`` call that names a column (keyword, dict key,
  ``set_={...}`` or a nested ``dict(col=...)``),
* an attribute assignment to a column (``row.twofa_password = x``) or ``setattr(row, "col", x)``,
* a SQL string that sets or inserts a column, or
* (conservative) a function (or the module body) that references the accounts table object or a
  column name AND calls ``.values(**x)``, ``.values(name)`` or ``execute(stmt, rows)``: the
  column may travel inside a dict built elsewhere. ``dynamic_ok`` whitelists a (file, function).
"""

from __future__ import annotations

import ast
import re
from typing import TYPE_CHECKING

from tests._custody_scan_base import parsed, tail

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_WRITERS = {"values", "update", "insert", "on_conflict_do_update"}
_EXECUTORS = {"execute", "executemany", "exec_driver_sql"}
_TABLE_NAMES = {"_accounts", "accounts_table"}
_MODULE = "<module>"


def _writes_column(node: ast.Call, columns: tuple[str, ...]) -> bool:
    if tail(node.func) not in _WRITERS:
        return False
    if any(keyword.arg in columns for keyword in node.keywords):
        return True
    for arg in [*node.args, *(keyword.value for keyword in node.keywords)]:
        for inner in ast.walk(arg):
            if isinstance(inner, ast.Dict) and any(
                isinstance(key, ast.Constant) and key.value in columns for key in inner.keys
            ):
                return True
            if isinstance(inner, ast.Call) and any(k.arg in columns for k in inner.keywords):
                return True
    return False


def _assigns_column(node: ast.AST, columns: tuple[str, ...]) -> bool:
    if isinstance(node, ast.Assign | ast.AugAssign | ast.AnnAssign):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return any(
            isinstance(leaf, ast.Attribute) and leaf.attr in columns
            for target in targets
            for leaf in ast.walk(target)
        )
    return (
        isinstance(node, ast.Call)
        and tail(node.func) == "setattr"
        and len(node.args) > 1
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value in columns
    )


def _sql_writes(text: str, columns: tuple[str, ...]) -> bool:
    return any(
        re.search(
            rf"\bSET\b.*\b{column}\b\s*=|\bINSERT\b.*\b{column}\b", text, re.DOTALL | re.IGNORECASE
        )
        for column in columns
    )


def _own_nodes(scope: ast.AST) -> Iterator[ast.AST]:
    """Nodes of a function or module body, not those of the functions nested in it."""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            stack.extend(ast.iter_child_nodes(node))


def _references(nodes: list[ast.AST], columns: tuple[str, ...]) -> bool:
    for node in nodes:
        if isinstance(node, ast.Name) and node.id in {*_TABLE_NAMES, *columns}:
            return True
        if isinstance(node, ast.Attribute) and node.attr in {*_TABLE_NAMES, *columns}:
            return True
        if isinstance(node, ast.keyword) and node.arg in columns:
            return True
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and any(column in node.value for column in columns)
        ):
            return True
    return False


def _dynamic_write(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    name = tail(node.func)
    if name == "values":
        return any(k.arg is None for k in node.keywords) or any(
            not isinstance(arg, ast.Dict | ast.Constant) for arg in node.args
        )
    return name in _EXECUTORS and len(node.args) > 1


def _dynamic_sites(tree: ast.Module, columns: tuple[str, ...]) -> set[str]:
    """Names of the scopes (functions, or the module body) with the conservative pattern."""
    scopes = [(_MODULE, tree)] + [
        (node.name, node)
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ]
    out: set[str] = set()
    for name, scope in scopes:
        nodes = list(_own_nodes(scope))
        if any(_dynamic_write(n) for n in nodes) and _references(nodes, columns):
            out.add(name)
    return out


def column_writers(
    root: Path,
    columns: tuple[str, ...],
    dynamic_ok: frozenset[tuple[str, str]] = frozenset(),
) -> set[str]:
    """Files that write one of ``columns``, see the module docstring for the shapes."""
    out: set[str] = set()
    for rel, tree in parsed(root).items():
        for node in ast.walk(tree):
            if (
                (isinstance(node, ast.Call) and _writes_column(node, columns))
                or _assigns_column(node, columns)
                or (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and _sql_writes(node.value, columns)
                )
            ):
                out.add(rel)
        if any((rel, scope) not in dynamic_ok for scope in _dynamic_sites(tree, columns)):
            out.add(rel)
    return out
