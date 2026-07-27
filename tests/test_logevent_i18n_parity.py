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


def _module_event_codes(source: str, path: Path) -> set[str]:
    """Every literal code passed to a ``log_event`` call in one module.

    An AST walk over the calls, not a pattern over the text: the level argument is
    NOT always a literal — ``services.proxies.check_proxy`` picks INFO/WARNING from
    the check outcome — and a regex anchored on a literal level silently dropped
    those calls, so ``proxy_checked`` shipped with no label in either locale and the
    operator read a raw snake_case code in the activity log. Reading the call's
    second positional argument cannot be defeated by the next formatting variation.

    A code that is itself not a literal (an f-string in the Telegram gateway, a
    caller-supplied event name) is skipped: the composed ones are covered by
    :func:`test_every_telegram_action_and_status_has_a_compositional_label`, and the
    rest cannot be resolved statically at all.
    """
    codes: set[str] = set()
    for node in ast.walk(ast.parse(source, filename=str(path))):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name != "log_event":
            continue
        # Positional in every current call site; the keyword form is accepted too so
        # the next call to spell it ``event=`` is not invisible either.
        code = next(
            (kw.value for kw in node.keywords if kw.arg == "event"),
            node.args[_CODE_ARG_INDEX] if len(node.args) > _CODE_ARG_INDEX else None,
        )
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


def test_every_telegram_action_and_status_has_a_compositional_label() -> None:
    actions = _action_types()
    assert actions, "discovered no telegram action_types — the regex or path is broken"
    for locale in ("ru", "en"):
        tg = _i18n(locale)["logEventTelegram"]
        missing_actions = sorted(actions - set(tg["action"]))
        missing_statuses = sorted(_TELEGRAM_STATUSES - set(tg["status"]))
        assert missing_actions == [], f"action labels missing in {locale}: {missing_actions}"
        assert missing_statuses == [], f"status labels missing in {locale}: {missing_statuses}"
