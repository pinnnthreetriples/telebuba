"""Drift guard: every backend ``log_event`` code must have a UI translation.

The API is locale-neutral — it emits stable snake_case event codes and the SPA
owns the labels (``frontend/src/shared/i18n/*.json`` under ``logEvent``, resolved
by ``eventLabel``). Because the codes are free-form strings (no enum), a new
``log_event`` call can silently regress to a raw snake_case code in the operator
UI. This test enumerates the backend codes and fails the build if any lacks a
Russian or English translation, so the gap is caught in CI rather than by an
operator.

The same applies to the second vocabulary the SPA owns: the ``extra["reason"]`` codes
rendered through ``logEventReason``. Those fail QUIETLY rather than loudly — the log
prefixes resolve with an empty default and only the toast ladder after them falls back to
the raw code (``eventReason.ts``'s ``label``), so a missing reason shows the operator a
snake_case token where prose belongs, and once did explain nothing at all (``too_short``).
What each scan can and cannot see is stated on :func:`_module_event_codes` and
:func:`_module_reason_codes`; read those before trusting this file to have caught
something. In particular ``_module_reason_codes`` sees literals and ``reason``-named
locals — NOT a name bound to a call result, which is why ``services.warming._reservation``
reports its two reservation losses as two event CODES instead.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import get_args

from services.accounts._import_rollback import RollbackOutcome

_TEST_TREE_ROOT = Path(__file__).resolve().parents[1]
# mutmut 3.6 runs the copied suite from ``<project>/mutants`` after expanding
# every function into its original plus all mutant implementations. This test is
# a source-tree drift guard, so parsing that expanded implementation would treat
# deliberately mutated event strings (for example ``ACCOUNT_ADDED``) as real
# production codes. Read the pristine parent tree in that one generated layout;
# regular pytest continues to inspect its own repository root.
_ROOT = _TEST_TREE_ROOT.parent if _TEST_TREE_ROOT.name == "mutants" else _TEST_TREE_ROOT
_CODE_ROOTS = ("api", "services", "core")
# ``log_event(level, code, ...)`` — the code is the second positional argument.
_CODE_ARG_INDEX = 1
# The Telegram gateway logs action outcomes with dynamically composed codes
# (``telegram_{action}`` / ``telegram_{action}_{status}``) that the SPA labels
# compositionally from ``logEventTelegram.action`` + ``.status`` — so those maps
# must cover every action_type and every status suffix, mirroring the suffix list
# in ``frontend/src/shared/lib/eventLabel.ts``.
_ACTION_TYPE = re.compile(r'action_type:\s*Literal\["([a-z_]+)"\]')
# ``extra["reason"]`` — the second operator-facing vocabulary, rendered through
# ``logEventReason.*``. See :func:`_module_reason_codes` for what a literal scan can and
# cannot see of it.
_REASON_KEY = "reason"
_EXTRA_KEY = "extra"
_TELEGRAM_STATUSES = frozenset(
    {"failed", "flood_wait", "slow_mode_wait", "premium_wait", "peer_flood", "already_participant"},
)
# ``services.neurocomment._outcomes._classify_post`` — every branch of the outcome ladder
# that reports a non-delivered post. Named here because that ladder is the reason the
# wrapper hop exists at all: it is the one place where a rename or a new branch is a
# label gap the operator meets before CI does. See the test that asserts it.
_OUTCOME_LADDER_CODES = frozenset(
    {
        "neurocomment_post_unavailable",
        "neurocomment_post_cooldown",
        "neurocomment_account_banned",
        "neurocomment_post_ban_unconfirmed",
        "neurocomment_post_access_lost",
        "neurocomment_post_gated",
        "neurocomment_post_failed",
    },
)


def _called_name(call: ast.Call) -> str:
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _calls_to(tree: ast.AST, name: str) -> list[ast.Call]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call) and _called_name(n) == name]


def _code_argument(call: ast.Call, index: int | None, keyword: str) -> ast.expr | None:
    """The argument carrying the event code, given where that call spells it."""
    keyed = next((kw.value for kw in call.keywords if kw.arg == keyword), None)
    if keyed is not None:
        return keyed
    if index is not None and len(call.args) > index:
        return call.args[index]
    return None


def _forwarding_wrappers(tree: ast.AST) -> dict[str, tuple[str, int | None]]:
    """Helpers that take the event code as a parameter and hand it to ``log_event``.

    Maps the helper's name to where IT spells the code: ``(parameter name, positional
    index)``, the index being None for a keyword-only parameter. Discovered, not listed
    by hand — a hard-coded name only catches the wrapper that already burned us.

    Why this matters: ``services.neurocomment._outcomes`` reports every non-delivered
    post through ``_log_outcome(..., event_name)``, so inside the wrapper the code is a
    variable and the walk below skipped it — ``neurocomment_post_unavailable`` shipped
    with no label in either locale while this whole module still passed. Same shape in
    ``services.accounts.login._end_session(..., event=...)``.

    Per-module by construction: both current wrappers are private and called only from
    the file that defines them, and keeping the scan local is what lets one module be
    checked from a source string. A wrapper called across modules would need a
    project-wide pass first.
    """
    wrappers: dict[str, tuple[str, int | None]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        positional = [a.arg for a in [*node.args.posonlyargs, *node.args.args]]
        parameters = {*positional, *(a.arg for a in node.args.kwonlyargs)}
        for call in _calls_to(node, "log_event"):
            code = _code_argument(call, _CODE_ARG_INDEX, "event")
            if isinstance(code, ast.Name) and code.id in parameters:
                wrappers[node.name] = (
                    code.id,
                    positional.index(code.id) if code.id in positional else None,
                )
    return wrappers


def _string_bindings(tree: ast.AST) -> dict[str, set[str]]:
    """Every string literal a plain name is assigned anywhere in one module.

    ``name = "literal"`` only, module-wide and flattened across scopes. That is enough for
    the shape this exists to catch — a code (or a reason) built up in a branch ladder and
    handed on as a variable — and a union across scopes is never *wrong*: the entries under
    one name are exactly the strings that name can carry in this file, all of which need a
    label. What it does NOT see: an unpacking target (``for _, event, _ in plans`` in
    ``services.warming._purge``), an attribute, an f-string, and a name bound to a call.
    """
    bindings: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if not (isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                bindings.setdefault(target.id, set()).add(node.value.value)
    return bindings


def _module_event_codes(source: str, path: Path) -> set[str]:
    """Every literal code passed to a ``log_event`` call in one module.

    An AST walk over the calls, not a pattern over the text: the level argument is
    NOT always a literal — ``services.proxies.check_proxy`` picks INFO/WARNING from
    the check outcome — and a regex anchored on a literal level silently dropped
    those calls, so ``proxy_checked`` shipped with no label in either locale and the
    operator read a raw snake_case code in the activity log. Reading the call's
    second positional argument cannot be defeated by the next formatting variation.

    THREE indirections are followed: the ``event_name`` prefixer below, every
    code-forwarding wrapper :func:`_forwarding_wrappers` finds (whose own call sites are
    then read the same way), and a plain local variable, resolved through
    :func:`_string_bindings`. That third one is why the wrapper hop is worth anything —
    ``_outcomes._classify_post`` assigns ``event_name`` in a seven-branch ladder and hands
    the VARIABLE to the wrapper, so following the wrapper but not the variable recovered
    one of the seven codes and this module claimed to guard all of them.

    Still skipped: an f-string (the Telegram gateway's composed names, covered instead by
    :func:`test_every_telegram_action_and_status_has_a_compositional_label`), a name bound
    by something other than a literal assignment — a parameter, a call result, a loop
    target (``for window_days, event, purge in plans`` in ``services.warming._purge``) —
    and a code imported from another module.
    """
    tree = ast.parse(source, filename=str(path))
    bindings = _string_bindings(tree)
    codes: set[str] = set()
    # Positional in every current call site; the keyword form is accepted too so
    # the next call to spell it ``event=`` is not invisible either.
    calls: list[tuple[ast.Call, int | None, str]] = [
        (call, _CODE_ARG_INDEX, "event") for call in _calls_to(tree, "log_event")
    ]
    calls += [
        (call, index, keyword)
        for wrapper, (keyword, index) in _forwarding_wrappers(tree).items()
        for call in _calls_to(tree, wrapper)
    ]
    for call, index, keyword in calls:
        code = _code_argument(call, index, keyword)
        # ``event_name(domain, "literal")``: the Telegram gateway runs its names through
        # that wrapper to prefix the calling domain, which would otherwise hide the
        # literal behind a Call node. The SPA strips the prefix and looks the bare name
        # up, so unwrap one level and keep the literal inside. Its f-string siblings stay
        # skipped, exactly as above — this widens discovery by one call shape, no more.
        if (
            isinstance(code, ast.Call)
            and getattr(code.func, "id", "") == "event_name"
            and len(code.args) > _CODE_ARG_INDEX
        ):
            code = code.args[_CODE_ARG_INDEX]
        if isinstance(code, ast.Constant) and isinstance(code.value, str):
            codes.add(code.value)
        elif isinstance(code, ast.Name):
            # The ladder shape: ``_classify_post`` picks ``event_name`` in one of seven
            # branches and forwards the variable, so the literal is at the ASSIGNMENT, not
            # at the call. Reading only the call argument recovered exactly one of those
            # seven codes and left the wrapper hop above almost pointless.
            codes |= bindings.get(code.id, set())
    return codes


def _backend_files() -> list[Path]:
    files = [path for root in _CODE_ROOTS for path in (_ROOT / root).rglob("*.py")]
    files.append(_ROOT / "main.py")
    return files


def _backend_event_codes() -> set[str]:
    codes: set[str] = set()
    for path in _backend_files():
        codes.update(_module_event_codes(path.read_text(encoding="utf-8"), path))
    return codes


def _is_reason_name(name: str) -> bool:
    """True for a name the codebase uses to carry a reason code (``reason``/``*_reason``)."""
    lowered = name.lower()
    return lowered == _REASON_KEY or lowered.endswith(f"_{_REASON_KEY}")


def _module_reason_codes(source: str, path: Path) -> set[str]:
    """Every literal reason code one module can put in ``extra["reason"]``.

    A second, separate vocabulary from the event codes: ``ActivityLogCard`` renders
    ``extra.reason`` through ``eventReason``, whose ``label`` tries the log prefixes with
    an empty default and then the TOAST prefixes with ``defaultValue: code`` — so an
    unmapped reason reaches the operator as a raw snake_case token where prose belongs,
    not as a blank. ``too_short`` (``services.neurocomment._generate``) is what this guard
    was written after; it is translated in both locales now (search ``logEventReason``).
    The genuinely blank fallback is a different key in a different component:
    ``WarmingBoard`` resolves ``extra.reaction_skip`` through ``logEventReason.*`` with
    ``defaultValue: ''``, so a miss there shows nothing at all. ``LogsPage`` prints an em
    dash for an empty reason, which is the third spelling of the same fallback.

    Two shapes are read, because they are how the codebase actually writes reasons: the
    value under a literal ``"reason"`` key of an ``extra={...}`` dict, and any literal a
    ``reason``/``*_reason`` NAME carries — the variable a branch ladder assigns
    (``_generate``'s regeneration ladder), the module constant (``_RATE_LIMITED_REASON``)
    and the return of a ``*_reason`` function (``_filters.filter_reason``,
    ``engine._selection_block_reason``). Names are resolved through
    :func:`_string_bindings`, so a variable forwarded into the dict is followed too. The
    ``extra=`` anchor is what keeps this to the LOG vocabulary: ``reason=`` is also a
    field on schemas the operator UI never renders through this map
    (``AccountChannelOnboarding``, ``PostImageResult``), and pulling those in would demand
    labels for codes nothing can display.

    NOT covered, and stated rather than papered over, because overclaiming coverage is the
    exact failure this guard was written after:

    * a reason COMPOSED at the call site — ``f"media_{image.reason}"`` in ``_generate``
      builds ``media_unavailable`` / ``media_too_large`` out of a ``PostImageResult``
      field, and no literal scan can see the whole code, only the halves;
    * a reason read off an attribute or an exception — ``exc.reason`` in ``_sweep``,
      ``type(exc).__name__`` and the stage errors in ``discovery``;
    * a literal handed to a positional field that happens to be named ``reason``
      (``engine._Selection(None, "no_accounts_linked")``);
    * a reason name imported from another module (``_generate`` returns ``_outcomes``'
      ``_RATE_LIMITED_REASON``) — bindings are per-module, so it is caught where it is
      DEFINED, and would be missed entirely if its owning module ever stopped logging;
    * ``extra["status"]``, which the same card resolves through the same ``logEventReason``
      map as a fallback. Its values come from the gateway's ``ActionResult.status``, not
      from a reason name, and are not enumerated here.
    """
    tree = ast.parse(source, filename=str(path))
    bindings = _string_bindings(tree)
    reasons: set[str] = set()

    def _literals(node: ast.expr | None) -> set[str]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}
        if isinstance(node, ast.Name):
            return bindings.get(node.id, set())
        if isinstance(node, ast.IfExp):
            # ``return None if vision_is_on else "media_no_caption"`` — one branch of a
            # ternary is where ``_filters`` hides the operator's own off-switch.
            return _literals(node.body) | _literals(node.orelse)
        return set()

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.keyword)
            and node.arg == _EXTRA_KEY
            and isinstance(node.value, ast.Dict)
        ):
            reasons |= {
                literal
                for key, value in zip(node.value.keys, node.value.values, strict=True)
                if isinstance(key, ast.Constant) and key.value == _REASON_KEY
                for literal in _literals(value)
            }
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and _is_reason_name(
            node.name
        ):
            reasons |= {
                literal
                for child in ast.walk(node)
                if isinstance(child, ast.Return)
                for literal in _literals(child.value)
            }
    for name, literals in bindings.items():
        if _is_reason_name(name):
            reasons |= literals
    return reasons


def _rollback_residual_codes() -> set[str]:
    """The import-rollback residuals, read off ``RollbackOutcome`` itself.

    Both call sites write ``extra={"reason": result.outcome}`` — an ATTRIBUTE read, and
    :func:`_module_reason_codes`'s ``_literals`` handles ``Constant``/``Name``/``IfExp``
    but not ``Attribute``, so the literal scan cannot see these two codes at all. They
    shipped untranslated-and-unnoticed exactly once, which is what this closes.

    Derived from the ``Literal`` rather than hardcoded, so a fourth outcome is caught
    the moment it is declared. ``clean`` is excluded because it is the success path:
    both callers log only when ``outcome != "clean"``, so it never reaches ``reason``.
    That exclusion is what makes the omission safe, so it is pinned behaviourally by
    ``tests/services/accounts/test_import_rollback.py``'s
    ``test_a_clean_rollback_emits_no_reason_code`` — on the emitted payload, because a
    source-text check for the guard passed with the guard removed and its literal left
    behind in a comment.
    """
    return set(get_args(RollbackOutcome)) - {"clean"}


def _backend_reason_codes() -> set[str]:
    reasons: set[str] = set()
    for path in _backend_files():
        reasons.update(_module_reason_codes(path.read_text(encoding="utf-8"), path))
    return reasons | _rollback_residual_codes()


def _i18n(locale: str) -> dict:
    path = _ROOT / "frontend" / "src" / "shared" / "i18n" / f"{locale}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _action_types() -> set[str]:
    src = (_ROOT / "schemas" / "telegram_actions.py").read_text(encoding="utf-8")
    return set(_ACTION_TYPE.findall(src))


def test_every_backend_log_event_has_ru_and_en_translation() -> None:
    codes = _backend_event_codes()
    assert codes, "discovered no backend log_event codes — the regex or paths are broken"
    for locale in ("ru", "en"):
        keys = set(_i18n(locale)["logEvent"])
        assert sorted(codes - keys) == [], f"codes missing a {locale} logEvent label"


def test_every_backend_reason_has_ru_and_en_translation() -> None:
    reasons = _backend_reason_codes()
    assert reasons, "discovered no backend reason codes — the scan or paths are broken"
    for locale in ("ru", "en"):
        keys = set(_i18n(locale)["logEventReason"])
        assert sorted(reasons - keys) == [], f"reasons missing a {locale} logEventReason label"


def test_reason_enumeration_reaches_a_ladder_variable_and_a_reason_function() -> None:
    """The two shapes that carry the reason vocabulary, probed on the real tree.

    ``too_short`` is the regression: ``services.neurocomment._generate`` assigns it in the
    regeneration ladder and logs it through ``outcome.reason``, so nothing literal is
    visible at the call site and it rendered as an EMPTY string next to
    ``neurocomment_generation_exhausted`` — the operator saw a failure with no reason at
    all. ``media_album_item`` is the other shape: a ``*_reason`` function's return, never
    assigned to anything named ``reason``.
    """
    source = (
        "def _pick_reason(kind):\n"
        "    return 'returned_reason'\n"
        "async def go():\n"
        "    reason = 'ladder_reason'\n"
        "    await log_event('INFO', 'e', extra={'reason': reason})\n"
        "    await log_event('INFO', 'e', extra={'reason': 'inline_reason'})\n"
    )
    assert _module_reason_codes(source, Path("probe.py")) == {
        "returned_reason",
        "ladder_reason",
        "inline_reason",
    }
    reasons = _backend_reason_codes()
    assert {"too_short", "media_album_item"} <= reasons


def test_enumeration_catches_a_code_logged_at_a_computed_level() -> None:
    """A level chosen at runtime must not hide its code from the parity check.

    The previous regex required a literal ``"INFO"``/``"WARNING"`` as the first
    argument, so ``services.proxies.check_proxy`` — which picks the level from the
    check outcome — was never enumerated and shipped an untranslated
    ``proxy_checked`` into the operator's activity log.
    """
    source = 'log_event("INFO" if ok else "WARNING", "computed_level_event")'
    assert _module_event_codes(source, Path("probe.py")) == {"computed_level_event"}
    assert "proxy_checked" in _backend_event_codes()


def test_enumeration_follows_a_code_through_a_logging_wrapper() -> None:
    """A module that logs through its own helper must not hide its codes either.

    ``services.neurocomment._outcomes`` reports every non-delivered post through
    ``_log_outcome(event, account_id, result, event_name)``. Inside that helper the code
    is a variable, so the call-site literal was never enumerated and
    ``neurocomment_post_unavailable`` shipped untranslated with this module green. Both
    spellings are probed — positional, as ``_outcomes`` writes it, and keyword-only, as
    ``services.accounts.login._end_session`` does.
    """
    source = (
        "async def _log_outcome(event, account_id, result, event_name):\n"
        "    await log_event('WARNING', event_name, account_id=account_id)\n"
        "async def _end_session(account_id, *, wipe_session, event):\n"
        "    await log_event('INFO', event)\n"
        "_log_outcome(e, a, r, 'wrapped_positional_event')\n"
        "_end_session(a, wipe_session=True, event='wrapped_keyword_event')\n"
    )
    assert _module_event_codes(source, Path("probe.py")) == {
        "wrapped_positional_event",
        "wrapped_keyword_event",
    }
    codes = _backend_event_codes()
    assert sorted(_OUTCOME_LADDER_CODES - codes) == []


def _family_keys(locale: str, family: str) -> set[str]:
    """The key set of one nested i18n family, e.g. ``neurocomment.channelStatus``."""
    node = _i18n(locale)
    for part in family.split("."):
        node = node[part]
    return set(node)


def test_channel_status_and_hint_keys_match_across_locales() -> None:
    """No operator-facing key family may ship in one locale only.

    All three are frontend-only, so nothing else guards them: ``neurocomment.channelStatus``
    is keyed by the backend ``ChannelStatus`` literal (tsc enforces that the badge's
    colour map is total, not that both locales carry the label), ``logEventHint`` is
    optional by construction — ``ActivityLogCard`` falls back to an empty string — and
    ``logEventTelegram.error`` is keyed by Telethon exception CLASS names, which no
    backend scan can enumerate (they arrive as ``type(exc).__name__``, never as a
    literal) and which fall back to the raw class name. A key added to one locale
    therefore renders as the raw key in the other with nothing failing anywhere.
    Symmetric difference: a key missing on EITHER side is a gap.
    """
    for family in ("neurocomment.channelStatus", "logEventHint", "logEventTelegram.error"):
        ru = _family_keys("ru", family)
        en = _family_keys("en", family)
        assert ru, f"{family} resolved to an empty map — the key path is broken"
        assert sorted(ru ^ en) == [], f"{family} keys missing from a locale"


def test_every_telegram_action_and_status_has_a_compositional_label() -> None:
    actions = _action_types()
    assert actions, "discovered no telegram action_types — the regex or path is broken"
    for locale in ("ru", "en"):
        tg = _i18n(locale)["logEventTelegram"]
        missing_actions = sorted(actions - set(tg["action"]))
        missing_statuses = sorted(_TELEGRAM_STATUSES - set(tg["status"]))
        assert missing_actions == [], f"action labels missing in {locale}: {missing_actions}"
        assert missing_statuses == [], f"status labels missing in {locale}: {missing_statuses}"
