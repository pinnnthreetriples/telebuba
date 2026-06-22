"""Unit tests for the pure helpers in ``features/warming/_termlog``.

The renderers there are UI-thin (``# pragma: no cover``); only the event→label
map, the ``extra`` formatter, and the expand-toggle carry logic worth pinning.
"""

from __future__ import annotations

import pytest

from features.warming._termlog import (
    _EVENT_LABEL,
    _event_label,
    _format_extra,
    _toggle_expanded,
)
from schemas.logs import LogEntry, LogStatus


def _entry(event: str, *, status: LogStatus = "success") -> LogEntry:
    return LogEntry(
        id=1,
        created_at="2026-06-22T22:16:00+00:00",
        level="INFO",
        status=status,
        account_id="acc-1",
        event=event,
        extra={},
    )


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ("telegram_set_online", ("wifi", "Онлайн")),
        ("telegram_join_channel", ("add_circle", "Вступил в канал")),
        ("telegram_read_channel", ("chrome_reader_mode", "Прочитал канал")),
        ("warming_cycle_completed", ("done_all", "Цикл завершён")),
        ("phase_advanced", ("trending_up", "Новая фаза")),
    ],
)
def test_event_label_known(event: str, expected: tuple[str, str]) -> None:
    assert _event_label(_entry(event)) == expected


def test_event_label_unknown_humanises() -> None:
    icon, label = _event_label(_entry("some_brand_new_event"))
    assert icon == "circle"
    assert label == "some brand new event"


def test_event_label_map_entries_are_nonempty_pairs() -> None:
    for icon, label in _EVENT_LABEL.values():
        assert icon
        assert label


def test_format_extra_renders_key_value() -> None:
    assert _format_extra({"joined": 2, "reads": 5}) == "joined=2 reads=5"


def test_format_extra_keeps_meaningful_falsy_values() -> None:
    # online=False / reactions=0 carry meaning and must survive.
    assert _format_extra({"online": False, "reactions": 0}) == "online=False reactions=0"


def test_format_extra_skips_empty_values() -> None:
    extra: dict[str, object] = {"channel": "durov", "note": "", "err": None, "tags": []}
    assert _format_extra(extra) == "channel=durov"


def test_format_extra_empty_dict_is_blank() -> None:
    assert _format_extra({}) == ""


def test_format_extra_caps_length() -> None:
    out = _format_extra({"message": "x" * 200})
    assert len(out) == 80
    assert out.endswith("…")


def test_toggle_expanded_flips_and_persists() -> None:
    state: dict[str, bool] = {}
    assert _toggle_expanded(state, "acc-1") is True
    assert state["acc-1"] is True
    assert _toggle_expanded(state, "acc-1") is False
    assert state["acc-1"] is False
