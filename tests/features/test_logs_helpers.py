"""Tests for the pure helpers behind the redesigned Logs page (spec §C.5).

The page render is UI-thin (``pragma: no cover``); the only branchy logic is the
``lvlMap`` badge derivation (``_level_badge``) and the HTML body builder
(``_table_body_html`` / ``_row_html``), so they carry the unit tests.
"""

from __future__ import annotations

import pytest

from features.logs import _level_badge, _row_html, _table_body_html
from schemas.logs import LogEntry


def _entry(**overrides: object) -> LogEntry:
    base: dict[str, object] = {
        "id": 1,
        "created_at": "2026-06-28 12:00:00",
        "level": "INFO",
        "status": "success",
        "account_id": "+7 921 553-20-11",
        "event": "warming_read_feed",
        "extra": {},
    }
    base.update(overrides)
    return LogEntry(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("level", "status", "label", "bg", "fg"),
    [
        ("INFO", "success", "OK", "#DDF7E9", "#12A150"),
        ("WARNING", "success", "OK", "#DDF7E9", "#12A150"),  # success wins over level
        ("ERROR", "error", "ERROR", "#FDE6E2", "#E5372A"),
        ("INFO", "error", "ERROR", "#FDE6E2", "#E5372A"),  # error status promotes to ERROR
        ("WARNING", "error", "ERROR", "#FDE6E2", "#E5372A"),  # error beats warn
        ("WARNING", "warning", "WARN", "#FFF0D2", "#E08700"),
        ("INFO", "warning", "WARN", "#FFF0D2", "#E08700"),
        # Defensive default: an INFO row with no recognised status → INFO badge.
        ("INFO", "info", "INFO", "#E1ECFF", "#0066FF"),
    ],
)
def test_level_badge_maps_to_lvlmap(
    level: str,
    status: str,
    label: str,
    bg: str,
    fg: str,
) -> None:
    assert _level_badge(level, status) == (label, bg, fg)


def test_level_badge_labels_are_uppercase() -> None:
    for level in ("INFO", "WARNING", "ERROR"):
        for status in ("success", "warning", "error"):
            badge_label, _, _ = _level_badge(level, status)
            assert badge_label == badge_label.upper()


def test_table_body_empty_shows_placeholder_and_no_rows() -> None:
    body = _table_body_html([])
    assert "Записей пока нет" in body
    assert "<tbody>" in body
    assert "tb-table" in body


def test_table_body_renders_headers() -> None:
    body = _table_body_html([])
    for header in ("Время", "Уровень", "Аккаунт", "Событие"):
        assert f">{header}<" in body


def test_row_html_humanises_event_and_shows_badge() -> None:
    row = _row_html(_entry(event="warming_read_feed", status="success"))
    assert "warming read feed" in row  # underscores → spaces
    assert "OK" in row
    assert "#DDF7E9" in row  # ok badge bg


def test_row_html_blank_account_falls_back_to_dash() -> None:
    row = _row_html(_entry(account_id=None))
    assert "—" in row


def test_row_html_escapes_dynamic_values() -> None:
    row = _row_html(_entry(event="boom <script>", status="error", level="ERROR"))
    assert "<script>" not in row
    assert "&lt;script&gt;" in row


def test_table_body_concatenates_all_rows() -> None:
    entries = [
        _entry(id=1, status="success", level="INFO"),
        _entry(id=2, status="error", level="ERROR", event="post_failed"),
    ]
    body = _table_body_html(entries)
    assert body.count("<tr>") == 3  # 1 header row + 2 body rows
    assert "ERROR" in body
    assert "OK" in body
