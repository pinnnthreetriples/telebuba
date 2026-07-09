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


def _backend_event_codes() -> set[str]:
    files = [path for root in _CODE_ROOTS for path in (_ROOT / root).rglob("*.py")]
    files.append(_ROOT / "main.py")
    codes: set[str] = set()
    for path in files:
        codes.update(_LOG_EVENT.findall(path.read_text(encoding="utf-8")))
    return codes


def _log_event_keys(locale: str) -> set[str]:
    path = _ROOT / "frontend" / "src" / "shared" / "i18n" / f"{locale}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return set(data["logEvent"])


def test_every_backend_log_event_has_ru_and_en_translation() -> None:
    codes = _backend_event_codes()
    assert codes, "discovered no backend log_event codes — the regex or paths are broken"
    assert sorted(codes - _log_event_keys("ru")) == [], "codes missing a Russian logEvent label"
    assert sorted(codes - _log_event_keys("en")) == [], "codes missing an English logEvent label"
