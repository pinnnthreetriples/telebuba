"""The API error contract — every operation declares exactly the errors it can answer.

``api/errors.py`` maps every raised exception onto one ``ErrorEnvelope`` body, and
routes declare the statuses they can answer with ``api.errors.error_responses(...)``
fragments. Nothing kept the two in step: 62 of the 91 operations misdescribed
themselves. 53 declared only FastAPI's auto-generated 422 ``HTTPValidationError`` —
whose ``detail`` key ``_handle_validation_error`` overwrites, so it never reaches the
wire — and no 401/500 at all; the accounts routers declared a blanket 400/404/503 on
routes such as ``/accounts/stats`` that answer none of them. The generated TypeScript
client copies whatever the schema says, so the SPA could not narrow an error without
guessing.

This module recomputes the reachable status set per operation *from the code* and
asserts the schema declares exactly that, with the envelope as the body:

* every ``raise HTTPException(status_code=...)`` reachable from the endpoint —
  transitively through the helpers it calls inside ``api/`` (``_decode_channel_id``,
  ``reject_oversized_upload``, ``service_errors_to_http``, ...) and through its
  ``Depends`` chain (``api.deps.get_current_user`` is where 401 lives);
* the statuses of the exception handlers registered in ``api/errors.py``, for the
  domain exceptions a reachable function re-raises (``AccountActionError``) and for
  the catch-all (500);
* 422 whenever FastAPI itself would document the auto validation response.

A new route with a wrong ``responses=`` goes red here.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from fastapi.dependencies.utils import get_flat_params
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from api import create_app
from services.accounts import AccountActionError

if TYPE_CHECKING:
    from fastapi.dependencies.models import Dependant

_ROOT = Path(__file__).resolve().parent.parent
_ENVELOPE_REF = "#/components/schemas/ErrorEnvelope"

# Statuses each handler registered by ``api.errors.register_error_handlers`` can
# answer. ``HTTPException`` carries its own status, so it contributes nothing here —
# the raise site does, and the AST scan below reads it.
# ``test_registered_exception_handlers_are_all_mapped`` fails if a handler is added
# to ``api/errors.py`` without an entry here, so this table cannot silently rot.
_HANDLER_STATUSES: dict[type[Exception], frozenset[int]] = {
    HTTPException: frozenset(),
    RequestValidationError: frozenset({422}),
    AccountActionError: frozenset({400, 503}),
    Exception: frozenset({500}),
}
_HANDLER_STATUSES_BY_NAME = {exc.__name__: statuses for exc, statuses in _HANDLER_STATUSES.items()}

# ``http_status.HTTP_404_NOT_FOUND`` -> 404. Routes use the named constants; the
# literal form is accepted too so a numeric ``status_code=404`` is not missed.
_STATUS_CONSTANT = re.compile(r"^HTTP_(\d{3})_")


@dataclass(frozen=True)
class _Facts:
    """What one ``api/`` function contributes to its callers' error surface."""

    statuses: frozenset[int]
    calls: frozenset[str]


def _literal_status(node: ast.expr | None) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if isinstance(node, ast.Attribute):
        match = _STATUS_CONSTANT.match(node.attr)
        if match is not None:
            return int(match.group(1))
    return None


def _exception_names(node: ast.expr | None) -> set[str]:
    """Bare class names mentioned in a ``raise``/``except`` clause."""
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Attribute):
        return {node.attr}
    if isinstance(node, ast.Tuple):
        return {name for element in node.elts for name in _exception_names(element)}
    return set()


def _handler_statuses_for(node: ast.expr | None) -> frozenset[int]:
    found: frozenset[int] = frozenset()
    for name in _exception_names(node):
        found |= _HANDLER_STATUSES_BY_NAME.get(name, frozenset())
    return found


def _http_exception_status(call: ast.Call) -> int | None:
    if not isinstance(call.func, ast.Name) or call.func.id != HTTPException.__name__:
        return None
    for keyword in call.keywords:
        if keyword.arg == "status_code":
            return _literal_status(keyword.value)
    return None


def _called_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _reraised_handler_statuses(node: ast.AST) -> frozenset[int]:
    """Statuses a ``raise`` contributes through a handler in ``api/errors.py``.

    Covers both ``raise SomeDomainError(...)`` and the bare ``raise`` inside an
    ``except SomeDomainError:`` block — which is how ``service_errors_to_http``
    passes ``AccountActionError`` on to its dedicated handler.
    """
    if isinstance(node, ast.Raise):
        raised = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
        return _handler_statuses_for(raised)
    if isinstance(node, ast.ExceptHandler) and any(
        isinstance(inner, ast.Raise) and inner.exc is None for inner in ast.walk(node)
    ):
        return _handler_statuses_for(node.type)
    return frozenset()


def _collect_facts(function: ast.FunctionDef | ast.AsyncFunctionDef) -> _Facts:
    statuses: set[int] = set()
    calls: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Call):
            name = _called_name(node)
            if name is not None:
                calls.add(name)
            status = _http_exception_status(node)
            if status is not None:
                statuses.add(status)
        statuses |= _reraised_handler_statuses(node)
    return _Facts(statuses=frozenset(statuses), calls=frozenset(calls))


def _module_name(path: Path) -> str:
    parts = path.relative_to(_ROOT).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


@cache
def _api_functions() -> dict[str, dict[str, _Facts]]:
    """``{module: {function: facts}}`` for every top-level function under ``api/``."""
    index: dict[str, dict[str, _Facts]] = {}
    for path in sorted((_ROOT / "api").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        index[_module_name(path)] = {
            node.name: _collect_facts(node)
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
    return index


@cache
def _api_imports() -> dict[str, dict[str, str]]:
    """``{module: {imported_name: defining_module}}`` for ``api``-internal imports."""
    index: dict[str, dict[str, str]] = {}
    for path in sorted((_ROOT / "api").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        index[_module_name(path)] = {
            alias.asname or alias.name: node.module
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("api")
            for alias in node.names
        }
    return index


def _resolve(module: str, name: str) -> tuple[str, str] | None:
    """Where ``name`` is defined, following one ``from api... import name`` hop."""
    if name in _api_functions().get(module, {}):
        return module, name
    imported_from = _api_imports().get(module, {}).get(name)
    if imported_from is not None and name in _api_functions().get(imported_from, {}):
        return imported_from, name
    return None


def _reachable_from(module: str, name: str) -> frozenset[int]:
    """Statuses raised by ``module.name`` or by any ``api/`` function it calls."""
    statuses: set[int] = set()
    seen: set[tuple[str, str]] = set()
    pending = [(module, name)]
    while pending:
        target = _resolve(*pending.pop())
        if target is None or target in seen:
            continue
        seen.add(target)
        facts = _api_functions()[target[0]][target[1]]
        statuses |= facts.statuses
        pending.extend((target[0], called) for called in facts.calls)
    return frozenset(statuses)


def _dependency_callables(dependant: Dependant) -> list[Any]:
    """The endpoint plus every ``Depends`` callable behind it, flattened.

    ``Any`` because the elements are read for ``__module__``/``__name__``, which
    ``Callable`` does not carry — they are plain functions at runtime.
    """
    calls: list[Any] = [] if dependant.call is None else [dependant.call]
    for sub in dependant.dependencies:
        calls.extend(_dependency_callables(sub))
    return calls


def _reachable_statuses(route: APIRoute) -> frozenset[int]:
    # The catch-all handler makes 500 possible on every operation.
    statuses = set(_HANDLER_STATUSES[Exception])
    # FastAPI documents its auto 422 whenever the operation has any parameter or a
    # body to validate; mirroring that rule here keeps the declaration honest about
    # what the framework can answer (and replaces its untrue ``HTTPValidationError``).
    if get_flat_params(route.dependant) or route.body_field:
        statuses |= _HANDLER_STATUSES[RequestValidationError]
    for call in _dependency_callables(route.dependant):
        statuses |= _reachable_from(call.__module__, call.__name__)
    return frozenset(statuses)


def _declared_statuses(operation: dict) -> frozenset[int]:
    return frozenset(
        int(status)
        for status in operation.get("responses", {})
        if status.isdigit() and int(status) >= 400
    )


def _documented_operations() -> list[tuple[APIRoute, dict]]:
    app = create_app()
    schema = app.openapi()
    out: list[tuple[APIRoute, dict]] = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.include_in_schema:
            continue
        method = next(iter(route.methods)).lower()
        out.append((route, schema["paths"][route.path][method]))
    return out


def test_every_operation_declares_exactly_its_reachable_error_statuses() -> None:
    mismatched = {}
    for route, operation in _documented_operations():
        declared = _declared_statuses(operation)
        reachable = _reachable_statuses(route)
        if declared != reachable:
            mismatched[route.operation_id] = {
                "undeclared": sorted(reachable - declared),
                "unreachable": sorted(declared - reachable),
            }
    assert mismatched == {}, (
        "these operations misdescribe their errors; declare the reachable statuses "
        f"with api.errors.error_responses(...): {mismatched}"
    )


def test_every_declared_error_response_is_the_error_envelope() -> None:
    """One error shape on the wire means one error schema in the document."""
    wrong = {}
    for route, operation in _documented_operations():
        for status, response in operation.get("responses", {}).items():
            if not status.isdigit() or int(status) < 400:
                continue
            schema = response.get("content", {}).get("application/json", {}).get("schema", {})
            if schema.get("$ref") != _ENVELOPE_REF:
                wrong[f"{route.operation_id} {status}"] = schema
    assert wrong == {}, f"error responses must be typed as ErrorEnvelope: {wrong}"


def test_registered_exception_handlers_are_all_mapped() -> None:
    """A new handler in ``api/errors.py`` must extend ``_HANDLER_STATUSES``.

    Without this, adding a handler for a new domain exception would widen what the
    routes can answer while the derivation above stayed blind to it, and the contract
    test would keep passing on a schema that had gone stale again.
    """
    ours = {
        exc
        for exc, handler in create_app().exception_handlers.items()
        if getattr(handler, "__module__", "") == "api.errors"
    }
    assert ours <= set(_HANDLER_STATUSES), (
        f"api/errors.py registers handlers with no status mapping: {ours - set(_HANDLER_STATUSES)}"
    )


def test_the_derivation_reaches_every_mapped_status() -> None:
    """Guard the deriver itself: a scan that stopped resolving would go quiet, not red.

    If ``_reachable_from`` ever failed to follow the call graph it would return the
    same small set everywhere, and the contract test would then demand that
    impoverished set be declared — green once the schema shrank to match.
    """
    found: frozenset[int] = frozenset()
    for route, _ in _documented_operations():
        found |= _reachable_statuses(route)
    assert found == {400, 401, 404, 409, 422, 429, 500, 503}
