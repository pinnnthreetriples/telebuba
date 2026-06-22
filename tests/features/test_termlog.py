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
    _humanize_detail,
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


def test_event_label_flood_family_collapses_to_one_label() -> None:
    # Any action's rate-limit variant maps to the same label without enumerating.
    for event in (
        "telegram_join_channel_flood_wait",
        "telegram_read_channel_slow_mode_wait",
        "telegram_send_dm_peer_flood",
    ):
        assert _event_label(_entry(event, status="warning")) == ("timer", "Лимит Telegram")


@pytest.mark.parametrize(
    ("event", "extra", "expected"),
    [
        (
            "telegram_join_channel_failed",
            {"message": 'No user has "x" as username'},
            "канал не найден",
        ),
        (
            "telegram_pool_connect_failed",
            {"error_type": "AuthKeyDuplicatedError", "message": "ignored"},
            "сессия использовалась с двух устройств — больше не работает",
        ),
        ("warming_cycle_completed", {"failures": 2, "status": "failed"}, "ошибок в цикле: 2"),
        ("telegram_join_channel_flood_wait", {"seconds": 30}, "ждём 30 с"),
        ("telegram_read_channel", {"channel": "durov"}, "durov"),
        ("warming_loop_crashed", {"message": "boom"}, "boom"),
        ("warming_started", {}, ""),
    ],
)
def test_humanize_detail(event: str, extra: dict[str, object], expected: str) -> None:
    assert _humanize_detail(event, extra) == expected


def test_humanize_detail_translates_reasons() -> None:
    out = _humanize_detail("warming_reconcile_not_ready", {"reasons": ["no proxy", "no channels"]})
    assert out == "нет прокси, нет каналов"


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
