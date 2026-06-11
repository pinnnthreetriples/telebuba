from __future__ import annotations

from types import SimpleNamespace

from features.accounts import (
    _account_id_from_event,
    _proxy_dialog_error,
    _proxy_dialog_geo,
    _proxy_dialog_status,
    _proxy_port_value,
    _remember_selection,
    _row_from_event,
)


def test_remember_selection_replaces_selected_ids() -> None:
    selected = {"old"}

    _remember_selection([{"account_id": "acc-1"}, {"account_id": "acc-2"}], selected)

    assert selected == {"acc-1", "acc-2"}


def test_account_id_from_event_accepts_raw_and_nicegui_args() -> None:
    assert _account_id_from_event("acc-1") == "acc-1"
    assert _account_id_from_event(SimpleNamespace(args=["acc-2"])) == "acc-2"
    assert _account_id_from_event(SimpleNamespace(args=None)) == ""


def test_row_from_event_accepts_dict_payload() -> None:
    row = {"account_id": "acc-1"}

    assert _row_from_event(SimpleNamespace(args=[row])) == row
    assert _row_from_event("not-a-row") == {}


def test_proxy_port_value_defaults_to_socks_port() -> None:
    assert _proxy_port_value({"proxy_port": 8000}) == 8000
    assert _proxy_port_value({}) == 1080


def test_proxy_dialog_helpers_render_route_status_and_error() -> None:
    row = {
        "proxy_status": "tcp_working",
        "proxy_last_checked_at": "2026-06-11T12:00:00+00:00",
        "proxy_country_name": "Netherlands",
        "proxy_country_code": "NL",
        "proxy_exit_ip": "45.130.253.155",
        "proxy_last_error": "connection refused",
    }

    assert _proxy_dialog_status(row) == "Status: working | checked 2026-06-11T12:00:00+00:00"
    assert _proxy_dialog_geo(row) == "Route: Netherlands | NL | 45.130.253.155"
    assert _proxy_dialog_error(row) == "Error: connection refused"


def test_proxy_dialog_helpers_render_empty_state() -> None:
    assert _proxy_dialog_status({}) == "Status: not checked"
    assert _proxy_dialog_geo({}) == "Route: no country/IP yet"
    assert _proxy_dialog_error({}) == ""
