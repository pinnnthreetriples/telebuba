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

import json
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_CODE_ROOTS = ("api", "services", "core")
_LOG_EVENT = re.compile(
    r'log_event\(\s*"(?:INFO|WARNING|ERROR|DEBUG)"\s*,\s*"([a-z0-9_]+)"',
    re.DOTALL,
)
# The Telegram gateway logs action outcomes with dynamically composed codes
# (``telegram_{action}`` / ``telegram_{action}_{status}``) that the SPA labels
# compositionally from ``logEventTelegram.action`` + ``.status`` — so those maps
# must cover every action_type and every status suffix, mirroring the suffix list
# in ``frontend/src/shared/lib/eventLabel.ts``.
_ACTION_TYPE = re.compile(r'action_type:\s*Literal\["([a-z_]+)"\]')
_TELEGRAM_STATUSES = frozenset(
    {"failed", "flood_wait", "slow_mode_wait", "premium_wait", "peer_flood", "already_participant"},
)


def _backend_event_codes() -> set[str]:
    files = [path for root in _CODE_ROOTS for path in (_ROOT / root).rglob("*.py")]
    files.append(_ROOT / "main.py")
    codes: set[str] = set()
    for path in files:
        codes.update(_LOG_EVENT.findall(path.read_text(encoding="utf-8")))
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


def test_every_telegram_action_and_status_has_a_compositional_label() -> None:
    actions = _action_types()
    assert actions, "discovered no telegram action_types — the regex or path is broken"
    for locale in ("ru", "en"):
        tg = _i18n(locale)["logEventTelegram"]
        missing_actions = sorted(actions - set(tg["action"]))
        missing_statuses = sorted(_TELEGRAM_STATUSES - set(tg["status"]))
        assert missing_actions == [], f"action labels missing in {locale}: {missing_actions}"
        assert missing_statuses == [], f"status labels missing in {locale}: {missing_statuses}"
