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
* every literal ``response.status_code = <int>`` assignment in a reachable ``api/``
  function. FastAPI lets a route answer a status by setting it on the injected
  ``Response`` instead of raising, and ``getReadiness`` does exactly that, so
  without this case its declared 503 read as unreachable;
* the statuses of the exception handlers registered in ``api/errors.py``, for the
  domain exceptions a reachable function re-raises (``AccountActionError``) and for
  the catch-all (500);
* 422 whenever FastAPI itself would document the auto validation response.

A new route with a wrong ``responses=`` goes red here.

One operation is exempt from the envelope half of the contract, listed in
``_ENVELOPE_EXEMPT`` with its reason. The exemption is a pair — operation id AND
status — so it cannot spread to another status on the same operation or the same
status elsewhere; ``test_the_envelope_exemption_does_not_cover_any_other_operation``
pins that.

Four limits, deliberate, so nobody mistakes green here for a proof:

1. **Import style matters.** ``_called_name`` records the bare attribute and
   ``_resolve`` only follows ``from api... import name``, so a *module-attribute*
   call — ``from api.v1 import _uploads`` then ``_uploads.reject_oversized_upload()``
   — is invisible and its raises go uncounted. No module under ``api/`` calls that
   way today, which is the only reason the scan is complete; keep importing the
   function, not the module.
2. **The scan stops at the layer edge.** It never reads ``services/``, yet
   ``AccountActionError`` has an app-wide handler. A future route that calls a
   service which raises it *without* going through ``service_errors_to_http`` would
   under-declare 400/503 and stay green. Wrap service calls in the mapper and this
   cannot happen; that is the contract the deriver actually checks.
3. **Entering the mapper is taken as proof of its statuses.** A route inside
   ``service_errors_to_http`` is credited 400/404/503 whether or not the service it
   wraps can raise them. So a mapper whose statuses the service cannot raise makes
   the route over-declare, and this test will insist on it — the fix is to drop the
   mapper, as ``set_all_accounts_privacy`` did (see the comment in its body: all that
   could still reach the mapper there was a corrupt-row read, which 500 answers more
   honestly than the 422 the mapper gave it), not to widen the deriver.
4. **The status-assignment case reads a LITERAL, in ``api/`` only.** It matches
   ``<anything>.status_code = <int constant or HTTP_nnn_ name>``. A status computed
   into a variable first, or set by something outside ``api/``, is invisible — the
   same blind spot the ``raise`` scan has, and for the same reason. Assign the
   literal at the route, as ``ready`` does.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
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
# Only 4xx/5xx participate in the error contract.
_MIN_ERROR_STATUS = 400

# The one declared non-2xx that is deliberately NOT an ``ErrorEnvelope``.
#
# ``GET /api/v1/ready`` is an orchestrator's readiness probe. The SPA never calls it,
# so it gains nothing from uniform narrowing — and its body IS the answer:
# ``{"status": "unavailable", "database": false}`` names which dependency is down.
# Wrapping that in the envelope would replace a per-dependency verdict with a
# generic code, i.e. delete the only thing the response says beyond its status.
#
# An allowlist of explicit (operation_id, status) pairs, because the alternative —
# relaxing the rule for every route — would let a real regression through silently.
# Adding a pair is a visible decision that has to be argued for here.
_ENVELOPE_EXEMPT: frozenset[tuple[str, str]] = frozenset({("getReadiness", "503")})

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


def _assigned_status(node: ast.AST) -> int | None:
    """A literal ``<something>.status_code = <int>`` — the non-raising way to answer.

    FastAPI routes may take a ``Response`` parameter and set the status on it instead
    of raising, which no ``raise`` scan can see. ``api.v1.health.ready`` answers 503
    that way, on purpose: it returns a typed per-dependency body, not an envelope.
    """
    if not isinstance(node, ast.Assign):
        return None
    for target in node.targets:
        if isinstance(target, ast.Attribute) and target.attr == "status_code":
            return _literal_status(node.value)
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
        assigned = _assigned_status(node)
        # Error statuses only: a route setting 200/201 this way is not part of the
        # error contract, and ``_declared_statuses`` would never list it.
        if assigned is not None and assigned >= _MIN_ERROR_STATUS:
            statuses.add(assigned)
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
    return {
        _module_name(path): {
            node.name: _collect_facts(node)
            for node in ast.parse(path.read_text(encoding="utf-8")).body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for path in sorted((_ROOT / "api").rglob("*.py"))
    }


@cache
def _api_imports() -> dict[str, dict[str, str]]:
    """``{module: {imported_name: defining_module}}`` for ``api``-internal imports."""
    return {
        _module_name(path): {
            alias.asname or alias.name: node.module
            for node in ast.parse(path.read_text(encoding="utf-8")).body
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("api")
            for alias in node.names
        }
        for path in sorted((_ROOT / "api").rglob("*.py"))
    }


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
        if status.isdigit() and int(status) >= _MIN_ERROR_STATUS
    )


def _non_envelope_responses(operation_id: str | None, responses: dict) -> dict[str, dict]:
    """Declared error responses whose body is not the envelope, exemptions removed.

    Split out from the test so the exemption's tightness can be asserted directly
    against synthetic operations, instead of only over whatever the app happens to
    declare today.
    """
    wrong: dict[str, dict] = {}
    for status, response in responses.items():
        if not status.isdigit() or int(status) < _MIN_ERROR_STATUS:
            continue
        if (operation_id, status) in _ENVELOPE_EXEMPT:
            continue
        schema = response.get("content", {}).get("application/json", {}).get("schema", {})
        if schema.get("$ref") != _ENVELOPE_REF:
            wrong[f"{operation_id} {status}"] = schema
    return wrong


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
        wrong |= _non_envelope_responses(route.operation_id, operation.get("responses", {}))
    assert wrong == {}, f"error responses must be typed as ErrorEnvelope: {wrong}"


_NOT_THE_ENVELOPE = {
    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ReadinessStatus"}}},
}


def test_the_envelope_exemption_does_not_cover_any_other_operation() -> None:
    """The carve-out is one operation and one status, not a hole in the rule.

    Asserted from both sides, because an exemption that leaked would make the
    envelope test go quiet rather than red — the failure mode that matters.
    """
    # The exempt pair is allowed through.
    assert _non_envelope_responses("getReadiness", {"503": _NOT_THE_ENVELOPE}) == {}
    # Nothing else is: not another status on the same operation, not the same status
    # on a different operation, not an ordinary route's ordinary error.
    assert _non_envelope_responses("getReadiness", {"500": _NOT_THE_ENVELOPE}) != {}
    assert _non_envelope_responses("getHealth", {"503": _NOT_THE_ENVELOPE}) != {}
    assert _non_envelope_responses("importAccountSession", {"400": _NOT_THE_ENVELOPE}) != {}
    # And the exemption never excuses a status the contract does not police anyway.
    assert _non_envelope_responses("getReadiness", {"200": _NOT_THE_ENVELOPE}) == {}


@pytest.mark.parametrize(
    "assignment",
    [
        "response.status_code = 503",
        "response.status_code = http_status.HTTP_503_SERVICE_UNAVAILABLE",
    ],
)
def test_a_literal_status_assignment_counts_as_reachable(assignment: str) -> None:
    """Both spellings of the non-raising answer, so neither reads as unreachable."""
    source = (
        f"async def probe(response):\n    if broken():\n        {assignment}\n    return None\n"
    )
    function = ast.parse(source).body[0]
    assert isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
    assert 503 in _collect_facts(function).statuses


def test_a_non_error_status_assignment_is_not_counted() -> None:
    """``response.status_code = 201`` is not an error declaration."""
    function = ast.parse("async def probe(response):\n    response.status_code = 201\n").body[0]
    assert isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
    assert _collect_facts(function).statuses == frozenset()


def test_a_forgotten_status_assignment_declaration_goes_red() -> None:
    """The new case has teeth: drop ``getReadiness``'s 503 and the contract disagrees.

    Without the ``response.status_code`` case the deriver saw no 503 at all and the
    contract test called the declaration *unreachable*; with it, the omission is what
    fails instead. This asserts the second direction directly.
    """
    route, operation = next(
        (r, o) for r, o in _documented_operations() if r.operation_id == "getReadiness"
    )
    reachable = _reachable_statuses(route)
    assert 503 in reachable
    assert _declared_statuses(operation) == reachable
    # The comparison the contract test makes would fail if the declaration were gone.
    assert _declared_statuses(operation) - {503} != reachable


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
