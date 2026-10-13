"""Chat pairs only accounts with a common language (Прогрев 2.0, item 4.2)."""

from __future__ import annotations

from datetime import UTC, datetime

from schemas.dialogues import DialoguePair
from services.dialogues import _build_pairs, _has_foreign_pair, _pairs_for_new_accounts

_LANGUAGES = {"es-1": "es", "es-2": "es", "es-3": "es", "ru-1": "ru", "ru-2": "ru", "x-1": None}


def _same_language(pairs: list[tuple[str, str]]) -> bool:
    return all(_LANGUAGES[a] == _LANGUAGES[b] for a, b in pairs)


def test_a_full_build_never_crosses_languages() -> None:
    for _ in range(30):
        pairs = _build_pairs(list(_LANGUAGES), _LANGUAGES)
        assert pairs
        assert _same_language(pairs)


def test_a_new_account_is_woven_in_with_its_own_language_only() -> None:
    for _ in range(30):
        pairs = _pairs_for_new_accounts(["ru-2"], list(_LANGUAGES), _LANGUAGES)
        assert pairs == [("ru-1", "ru-2")]


def test_an_account_without_a_language_stays_alone() -> None:
    assert _pairs_for_new_accounts(["x-1"], list(_LANGUAGES), _LANGUAGES) == []


def test_without_languages_everyone_may_pair() -> None:
    pairs = _build_pairs(["es-1", "ru-1"])
    assert pairs == [("es-1", "ru-1")]


def test_a_cross_language_pair_from_before_is_spotted() -> None:
    now = datetime.now(UTC).isoformat()
    foreign = [DialoguePair(account_a="es-1", account_b="ru-1", assigned_at=now)]
    native = [DialoguePair(account_a="es-1", account_b="es-2", assigned_at=now)]
    gone = [DialoguePair(account_a="es-1", account_b="left", assigned_at=now)]
    assert _has_foreign_pair(foreign, _LANGUAGES)
    assert not _has_foreign_pair(native, _LANGUAGES)
    assert not _has_foreign_pair(gone, _LANGUAGES)
