"""Drift guard: every backend ``log_event`` code must have a UI translation.

The API is locale-neutral — it emits stable snake_case event codes and the SPA
owns the labels (``frontend/src/shared/i18n/*.json`` under ``logEvent``, resolved
by ``eventLabel``). Because the codes are free-form strings (no enum), a new
``log_event`` call can silently regress to a raw snake_case code in the operator
UI. This test enumerates the backend codes and fails the build if any lacks a
Russian or English translation, so the gap is caught in CI rather than by an
operator.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_CODE_ROOTS = ("api", "services", "core")
# ``log_event(level, code, ...)`` — the code is the second positional argument.
_CODE_ARG_INDEX = 1
# The Telegram gateway logs action outcomes with dynamically composed codes
# (``telegram_{action}`` / ``telegram_{action}_{status}``) that the SPA labels
# compositionally from ``logEventTelegram.action`` + ``.status`` — so those maps
# must cover every action_type and every status suffix, mirroring the suffix list
# in ``frontend/src/shared/lib/eventLabel.ts``.
_ACTION_TYPE = re.compile(r'action_type:\s*Literal\["([a-z_]+)"\]')
_TELEGRAM_STATUSES = frozenset(
    {"failed", "flood_wait", "slow_mode_wait", "premium_wait", "peer_flood", "already_participant"},
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


def _module_event_codes(source: str, path: Path) -> set[str]:
    """Every literal code passed to a ``log_event`` call in one module.

    An AST walk over the calls, not a pattern over the text: the level argument is
    NOT always a literal — ``services.proxies.check_proxy`` picks INFO/WARNING from
    the check outcome — and a regex anchored on a literal level silently dropped
    those calls, so ``proxy_checked`` shipped with no label in either locale and the
    operator read a raw snake_case code in the activity log. Reading the call's
    second positional argument cannot be defeated by the next formatting variation.

    A code that is itself not a literal (an f-string in the Telegram gateway, a
    caller-supplied event name, the local variable ``services.neurocomment._generate``
    assigns before logging) is skipped: the composed ones are covered by
    :func:`test_every_telegram_action_and_status_has_a_compositional_label`, and the
    rest cannot be resolved statically at all. TWO call shapes are unwrapped — the
    ``event_name`` prefixer below, and every code-forwarding wrapper
    :func:`_forwarding_wrappers` finds, whose own call sites are then read the same way.
    """
    tree = ast.parse(source, filename=str(path))
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
    return codes


def _backend_event_codes() -> set[str]:
    files = [path for root in _CODE_ROOTS for path in (_ROOT / root).rglob("*.py")]
    files.append(_ROOT / "main.py")
    codes: set[str] = set()
    for path in files:
        codes.update(_module_event_codes(path.read_text(encoding="utf-8"), path))
    return codes


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
    assert "neurocomment_post_unavailable" in _backend_event_codes()


def _family_keys(locale: str, family: str) -> set[str]:
    """The key set of one nested i18n family, e.g. ``neurocomment.channelStatus``."""
    node = _i18n(locale)
    for part in family.split("."):
        node = node[part]
    return set(node)


def test_channel_status_and_hint_keys_match_across_locales() -> None:
    """Neither operator-facing key family may ship in one locale only.

    Both are frontend-only, so nothing else guards them: ``neurocomment.channelStatus``
    is keyed by the backend ``ChannelStatus`` literal (tsc enforces that the badge's
    colour map is total, not that both locales carry the label), and ``logEventHint`` is
    optional by construction — ``ActivityLogCard`` falls back to an empty string. A key
    added to one locale therefore renders as the raw key in the other with nothing
    failing anywhere. Symmetric difference: a key missing on EITHER side is a gap.
    """
    for family in ("neurocomment.channelStatus", "logEventHint"):
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
