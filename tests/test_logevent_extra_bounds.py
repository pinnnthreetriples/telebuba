"""Drift guard: ``log_event``/``signal_event`` ``extra`` must not carry exception prose.

``log_event`` is not log-only. ``core.logging`` persists every event to the ``logs``
table with ``extra`` serialised as JSON, ``GET /logs`` serves it back as
``LogEntry.extra`` (``schemas.logs.LogEntry.extra: dict[str, object]``) and
``GET /events`` streams whole entries over SSE. So **anything in ``extra`` is an HTTP
response body**, and ``signal_event`` — publish-without-insert — is the SSE half of
the same surface.

``str(exc)`` / ``repr(exc)`` on a third-party exception is unbounded prose composed
from the raising frame: for this project's dependencies it carries a proxy endpoint,
a proxy URL with ``user:pass@host:port``, or an absolute ``.session`` path. Putting
it in ``extra`` publishes it (non-negotiable #12: never expose secrets, sessions,
tdata, JWTs or proxy credentials — OWASP's logging guidance says the same).

**The rule.** ``extra`` carries bounded, operator-actionable values only: stable
codes, exception CLASS names (``type(exc).__name__``, the established form — the SPA
reads an ``error_type`` key in ``ActivityLogCard.tsx`` and ``WarmingBoard.tsx``),
counts and ids. Full third-party text goes to the module's **stdlib** logger, which
``core.logging.setup_logging`` bridges into loguru's rotating ``debug.log`` while
leaving it on the uvicorn console — retained on disk, served by no route.
``core.proxy_check._failed_result``, ``core.tdata_import._bounded_conversion_error``
and ``core.telegram_client._profile._mark_account_status`` document the same sink.

The rule is about the **route**, not the column: ``services.warming._runner`` bounds
``WarmingAccountState.last_error`` the same way, because that model is the body of
``GET /warming/board``. This gate only reads ``extra``, so a persisted field like that
one is outside it — check the response model by hand.

**Not a sanitiser.** There is deliberately no redaction pass over ``extra``:
heuristic redaction over-redacts or misses, and hides the rule instead of stating
it. Bound at the call site, where the author can see what the value is.

**What the detector flags** (:func:`_module_violations`). A name is an *exception
name* where the source says so: bound by ``except ... as <name>``, or a function
parameter annotated with an exception class (``Exception``, ``BaseException``, or a
class named ``*Error``/``*Exception`` — this is how ``api.errors._handle_unexpected``
and ``core.telegram_client._action_results`` receive theirs; :func:`_annotation_names`
unwraps the union / dotted / quoted spellings of that). Within that scope, an
``extra=`` dict literal on a ``log_event``/``signal_event`` call is flagged when a
value is the exception object rendered as text:

* ``str(<name>)`` / ``repr(<name>)``, and the same over ``__cause__`` / ``__context__``,
* the bare name ``<name>``,
* an f-string interpolating any of those bare (``f"...{exc}..."``, any conversion),
* a local variable assigned one of those in the same scope, directly or through a
  conditional (``cause = str(exc.__cause__) if exc.__cause__ else None``).

**What this gate is not.** It is a *drift guard*, not a soundness proof, and it should
be read as one. It recognises the handful of shapes a regression actually takes —
someone types ``"message": str(exc)`` or ``f"{exc}"`` next to an ``error_type`` that is
already there — and it is deliberately precise, because a false positive that forces an
allowlist onto every corrected call site is worse than no gate at all. There is no
allowlist and no ``noqa`` escape, and none is needed. **A green run means no known
shape is present, not that no exception text can reach a route.** A reviewer still has
to look.

What it does not see — roughly a dozen and a half distinct shapes, in these categories:

* **one layer of wrapping** around the rendered exception — ``str(exc) % x``,
  ``"e: " + str(exc)``, ``"{}".format(exc)``, ``str(exc)[:80]``, ``str(exc).lower()``,
  ``[str(exc)]``, ``{"m": str(exc)}`` as a nested value;
* **a non-literal or nested ``extra``** — a dict built in a local variable and passed
  by name, a ``dict(...)`` call, a ``{**spread}`` entry (see the ``key is None`` note
  in :func:`_scope_violations`);
* **a helper that renders the exception** — the value is a call to a local function, or
  an attribute of a result object the helper filled in (``extra={"error":
  result.error}`` where ``result.error`` was built from ``f"{exc}"`` elsewhere; this
  really exists, via ``core.gemini`` → ``services.warming._chat_text``). Catching those
  needs a different detector and carries real false-positive risk, so it is its own
  decision, not a widening of this one;
* **an aliased import or a rebound name** — ``from core.logging import log_event as
  emit``, or a stringifier shadowed locally.

It also, on purpose, does not flag ``type(exc).__name__`` even though ``exc`` appears in
that subtree (the interpolated expression is an ``Attribute``, not the bare ``Name``),
nor ``str()`` of anything the module computed itself. Widen the shape list when a real
call site appears; do not guess, and do not trade the zero-false-positive property for
coverage.
"""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_CODE_ROOTS = ("api", "services", "core")
_LOGGERS = frozenset({"log_event", "signal_event"})
_STRINGIFIERS = frozenset({"str", "repr"})
_CHAINS = frozenset({"__cause__", "__context__"})
_EXCEPTION_BASES = frozenset({"Exception", "BaseException"})


def _annotation_names(node: ast.expr | None) -> tuple[str, ...]:
    """Every bare class name this annotation could resolve to.

    Unwrapping matters more than it looks: an unrecognised annotation does not just
    skip one parameter, it makes the WHOLE function body invisible to the detector.
    ``ast.Name`` alone missed a union (``exc: Exception | None`` — a ``BinOp``) and a
    dotted name (``exc: errors.RPCError``, which
    ``core.telegram_client._channels._map_telethon_error`` really uses).
    """
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, ast.Attribute):  # ``errors.RPCError``
        return (node.attr,)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):  # ``exc: "Exception"``
        return (node.value.rpartition(".")[2],)
    if isinstance(node, ast.BinOp):  # ``Exception | None``
        return _annotation_names(node.left) + _annotation_names(node.right)
    if isinstance(node, ast.Subscript):  # ``Optional[Exception]``
        return _annotation_names(node.slice)
    return ()


def _is_exception_annotation(node: ast.expr | None) -> bool:
    """Does the source declare this parameter to be an exception object?"""
    return any(
        name in _EXCEPTION_BASES or name.endswith(("Error", "Exception"))
        for name in _annotation_names(node)
    )


def _is_exception_expr(node: ast.expr, names: frozenset[str]) -> bool:
    return (isinstance(node, ast.Name) and node.id in names) or (
        isinstance(node, ast.Attribute)
        and node.attr in _CHAINS
        and _is_exception_expr(node.value, names)
    )


def _is_rendered(node: ast.expr, names: frozenset[str]) -> bool:
    """Is ``node`` one of the exception objects in ``names``, as text?"""
    if isinstance(node, ast.Name):
        return node.id in names
    if isinstance(node, ast.Call):
        return (
            getattr(node.func, "id", "") in _STRINGIFIERS
            and len(node.args) == 1
            and _is_exception_expr(node.args[0], names)
        )
    if isinstance(node, ast.JoinedStr):
        # Only a BARE interpolation: ``f"{exc}"``. ``f"{type(exc).__name__}"`` also
        # contains a ``Name`` node for ``exc``, so a subtree search would flag the
        # very form this rule prescribes.
        return any(
            isinstance(part, ast.FormattedValue) and _is_rendered(part.value, names)
            for part in node.values
        )
    if isinstance(node, ast.IfExp):
        return _is_rendered(node.body, names) or _is_rendered(node.orelse, names)
    return False


def _scope_violations(body: list[ast.stmt], names: frozenset[str], path: Path) -> set[str]:
    block = ast.Module(body=body, type_ignores=[])
    # One alias pass: ``cause = str(exc.__cause__) if ... else None`` then
    # ``extra={"cause": cause}`` is the same leak spelled over two statements.
    aliases = {
        target.id
        for node in ast.walk(block)
        if isinstance(node, ast.Assign) and _is_rendered(node.value, names)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    names |= aliases
    found: set[str] = set()
    for node in ast.walk(block):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if called not in _LOGGERS:
            continue
        extra = next((kw.value for kw in node.keywords if kw.arg == "extra"), None)
        if not isinstance(extra, ast.Dict):
            continue
        for key, value in zip(extra.keys, extra.values, strict=True):
            # ``key is None`` is a ``{**spread}`` entry. Skipped because there is no key
            # to name in the report and the matcher does not descend into the spread —
            # NOT because it is safe: ``extra={**{"m": str(exc)}}`` is one of the misses
            # the module docstring lists. A reviewer, not this line, is the check there.
            if key is None or not _is_rendered(value, names):
                continue
            label = key.value if isinstance(key, ast.Constant) else ast.unparse(key)
            found.add(f"{path.relative_to(_ROOT).as_posix()}:{value.lineno} {label}")
    return found


def _module_violations(source: str, path: Path) -> list[str]:
    """``file:line key`` for every rendered-exception value in an ``extra`` dict.

    Scopes are visited independently, each against the names IT introduces; a nested
    scope's body is a subset of the enclosing one, so a call that stringifies an outer
    ``exc`` from an inner handler is still caught.
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(source, filename=str(path))):
        if isinstance(node, ast.ExceptHandler) and node.name is not None:
            found |= _scope_violations(node.body, frozenset({node.name}), path)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            params = frozenset(
                arg.arg
                for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
                if _is_exception_annotation(arg.annotation)
            )
            if params:
                found |= _scope_violations(node.body, params, path)
    return sorted(found)


def _backend_violations() -> list[str]:
    files = [path for root in _CODE_ROOTS for path in (_ROOT / root).rglob("*.py")]
    files.append(_ROOT / "main.py")
    violations: list[str] = []
    for path in sorted(files):
        violations.extend(_module_violations(path.read_text(encoding="utf-8"), path))
    return violations


def test_no_log_event_extra_carries_exception_prose() -> None:
    violations = _backend_violations()
    assert violations == [], (
        "these log_event/signal_event calls publish third-party exception text through "
        "GET /logs and GET /events - keep type(exc).__name__ in extra and send the full "
        "text to the module's stdlib logger (see this module's docstring):\n"
        + "\n".join(violations)
    )


def test_detector_flags_every_unbounded_shape() -> None:
    source = (
        "try:\n"
        "    work()\n"
        "except Exception as exc:\n"
        '    await log_event("ERROR", "e", extra={"a": str(exc), "b": repr(exc)})\n'
        '    await log_event("ERROR", "e", extra={"c": exc, "d": f"boom: {exc}"})\n'
        '    signal_event("e", extra={"e": f"{exc!r}"})\n'
    )
    assert [entry.split(" ", 1)[1] for entry in _module_violations(source, _ROOT / "p.py")] == [
        "a",
        "b",
        "c",
        "d",
        "e",
    ]


def test_detector_sees_wrapped_exception_annotations() -> None:
    """A union / dotted / quoted annotation must not blind the whole function body.

    ``ast.Name``-only matching returned nothing for all three of these, and the miss is
    not one parameter — it is every ``log_event`` in that function.
    ``_channels._map_telethon_error(exc: errors.RPCError)`` is the dotted case, live in
    the tree today.
    """
    source = (
        "async def a(exc: Exception | None) -> None:\n"
        '    await log_event("E", "e", extra={"a": str(exc)})\n'
        "async def b(exc: errors.RPCError) -> None:\n"
        '    await log_event("E", "e", extra={"b": str(exc)})\n'
        'async def c(exc: "BaseException") -> None:\n'
        '    await log_event("E", "e", extra={"c": f"{exc}"})\n'
    )
    assert [entry.split(" ", 1)[1] for entry in _module_violations(source, _ROOT / "p.py")] == [
        "a",
        "b",
        "c",
    ]


def test_detector_ignores_non_exception_annotations() -> None:
    """The unwrapping must not turn every annotated parameter into an exception name."""
    source = (
        "async def a(payload: str | None, count: dict[str, int]) -> None:\n"
        '    await log_event("E", "e", extra={"a": payload, "b": str(count)})\n'
    )
    assert _module_violations(source, _ROOT / "p.py") == []


def test_detector_accepts_the_prescribed_form() -> None:
    """The class name is the fix, so it must never trip the gate.

    ``f"{type(exc).__name__}"`` holds a ``Name`` node for ``exc`` inside the
    interpolation — a subtree search would flag it and force an allowlist onto every
    corrected call site, which is the failure mode this detector is shaped to avoid.
    """
    source = (
        "try:\n"
        "    work()\n"
        "except Exception as exc:\n"
        '    await log_event("ERROR", "e", extra={\n'
        '        "error_type": type(exc).__name__,\n'
        '        "label": f"{type(exc).__name__} while probing",\n'
        '        "count": len(items),\n'
        '        "code": str(RETRY_CODE),\n'
        "    })\n"
        "    logger.exception"
        '("probe failed (error_type=%s): %s", type(exc).__name__, exc)\n'
    )
    assert _module_violations(source, _ROOT / "p.py") == []
