"""Tests for the pure cell-display derivations added in the accounts redesign.

The Vue cell templates render precomputed fields (avatar initials/class,
status-pill colours, parsed phone/handle, proxy dot/flag/short-label, device
line) so the markup stays logic-free. Those derivations live in
``features.accounts._table_cells`` and are exercised here.
"""

from __future__ import annotations

from features.accounts._table_cells import (
    _avatar_initials,
    _device_display,
    _health_avatar_class,
    _health_pill_colors,
    _phone_and_handle,
    _proxy_dot_color,
    _proxy_flag_cc,
    _proxy_flag_url,
    _proxy_label_short,
    _to_table_row,
)


def test_avatar_initials_take_last_two_phone_digits() -> None:
    assert _avatar_initials("+7 921 553-20-11") == "11"
    assert _avatar_initials("+7 905 118-44-90") == "90"
    # No digits at all → safe placeholder, never an empty avatar.
    assert _avatar_initials("без номера") == "?"
    assert _avatar_initials("") == "?"


def test_health_avatar_class_maps_traffic_light() -> None:
    assert _health_avatar_class("ok") == "tb-acc-av-active"
    assert _health_avatar_class("fail") == "tb-acc-av-banned"
    assert _health_avatar_class("warn") == "tb-acc-av-code"
    # Unknown health falls back to the neutral grey avatar.
    assert _health_avatar_class("mystery") == "tb-acc-av-code"


def test_health_pill_colors_match_status_map() -> None:
    assert _health_pill_colors("ok") == ("#DDF7E9", "#12A150")
    assert _health_pill_colors("fail") == ("#FDE6E2", "#E5372A")
    assert _health_pill_colors("warn") == ("#FFF0D2", "#E08700")
    # Unknown → amber (the "needs attention" default).
    assert _health_pill_colors("mystery") == ("#FFF0D2", "#E08700")


def test_phone_and_handle_splits_service_label() -> None:
    # Full "Name | @handle | phone" → (phone, handle).
    assert _phone_and_handle("Оператор 01 | @warm_oper_01 | +7 921 553-20-11") == (
        "+7 921 553-20-11",
        "@warm_oper_01",
    )
    # No handle.
    assert _phone_and_handle("Оператор | +7 905 118-44-90") == ("+7 905 118-44-90", "")
    # Handle but no phone → first non-handle part becomes the primary line.
    assert _phone_and_handle("Оператор | @just_handle") == ("Оператор", "@just_handle")
    # Empty / sentinel → em-dash, never blank.
    assert _phone_and_handle("") == ("—", "")
    assert _phone_and_handle("-") == ("—", "")


def test_device_display_joins_with_middots() -> None:
    assert _device_display("iPhone 13 | iOS 17.2 | 10.2") == "iPhone 13 · iOS 17.2 · 10.2"
    assert _device_display("-") == "—"
    assert _device_display("") == "—"


def test_proxy_dot_color_maps_status() -> None:
    assert _proxy_dot_color("tcp_working") == "#2E9E64"
    assert _proxy_dot_color("failed") == "#C0473F"
    assert _proxy_dot_color("unknown") == "#C9C9CE"
    assert _proxy_dot_color(None) == "#C9C9CE"


def test_proxy_flag_cc_and_url_only_accept_two_letter_codes() -> None:
    assert _proxy_flag_cc("NL") == "nl"
    assert _proxy_flag_cc("us") == "us"
    assert _proxy_flag_cc("USA") == ""  # not 2 letters
    assert _proxy_flag_cc("") == ""
    assert _proxy_flag_cc(None) == ""
    assert _proxy_flag_url("NL") == "url(https://flagcdn.com/nl.svg)"
    assert _proxy_flag_url("USA") == ""


def test_proxy_label_short_prefers_cc_then_type() -> None:
    assert _proxy_label_short({"proxy_country_code": "NL", "proxy_type": "socks5"}) == "NL · SOCKS5"
    assert _proxy_label_short({"proxy_type": "https"}) == "HTTPS"
    assert _proxy_label_short({"proxy_country_code": "DE"}) == "DE"
    assert _proxy_label_short({}) == "—"


def test_to_table_row_precomputes_all_display_fields() -> None:
    row: dict[str, object] = {
        "account_id": "acc-1",
        "status": "alive",
        "health": "ok",
        "telegram": "Оператор | @oper_01 | +7 921 553-20-11",
        "device": "iPhone 13 | iOS 17.2",
        "last_checked": "5 мин назад",
        "proxy_host": "nl-1.proxyhub.net",
        "proxy_type": "socks5",
        "proxy_status": "tcp_working",
        "proxy_country_code": "NL",
    }

    out = _to_table_row(row)

    assert out["status"] == "Живой"
    assert out["phone_display"] == "+7 921 553-20-11"
    assert out["handle_display"] == "@oper_01"
    assert out["avatar_initials"] == "11"
    assert out["avatar_class"] == "tb-acc-av-active"
    assert out["status_bg"] == "#DDF7E9"
    assert out["status_fg"] == "#12A150"
    assert out["device_display"] == "iPhone 13 · iOS 17.2"
    assert out["proxy_dot"] == "#2E9E64"
    assert out["proxy_flag_url"] == "url(https://flagcdn.com/nl.svg)"
    assert out["proxy_label_short"] == "NL · SOCKS5"
    # Input dict is not mutated.
    assert row["status"] == "alive"


def test_to_table_row_tolerates_minimal_dict() -> None:
    # Defensive: a sparse row (no telegram/health/proxy) must not raise and must
    # still produce safe placeholders — the existing helper tests pass such dicts.
    sparse: dict[str, object] = {"status": "new", "last_checked": "never"}
    out = _to_table_row(sparse)

    assert out["status"] == "Новый"
    assert out["last_checked"] == "никогда"
    assert out["phone_display"] == "—"
    assert out["handle_display"] == ""
    assert out["avatar_initials"] == "?"
    assert out["avatar_class"] == "tb-acc-av-code"
    assert out["device_display"] == "—"
    assert out["proxy_label_short"] == "—"
