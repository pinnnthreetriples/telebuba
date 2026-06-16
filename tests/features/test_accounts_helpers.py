from __future__ import annotations

from types import SimpleNamespace

from features.accounts._dialogs import (
    _proxy_dialog_error,
    _proxy_dialog_geo,
    _proxy_dialog_status,
    _proxy_port_value,
)
from features.accounts._table import (
    _account_id_from_event,
    _account_status_label,
    _remember_selection,
    _row_from_event,
    _service_error_label,
    _to_table_row,
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

    assert _proxy_dialog_status(row) == "Статус: работает | проверено 2026-06-11T12:00:00+00:00"
    assert _proxy_dialog_geo(row) == "Маршрут: Netherlands | NL | 45.130.253.155"
    assert _proxy_dialog_error(row) == "Ошибка: connection refused"


def test_proxy_dialog_helpers_render_empty_state() -> None:
    assert _proxy_dialog_status({}) == "Статус: не проверен"
    assert _proxy_dialog_geo({}) == "Маршрут: страна/IP пока неизвестны"
    assert _proxy_dialog_error({}) == ""


def test_account_status_label_translates_known_and_falls_back() -> None:
    assert _account_status_label("alive") == "Живой"
    assert _account_status_label("flood_wait") == "FloodWait"
    # Unknown status: humanised passthrough (underscores → spaces).
    assert _account_status_label("brand_new_state") == "brand new state"


def test_to_table_row_translates_status_and_never() -> None:
    row: dict[str, object] = {"status": "alive", "last_checked": "never", "account_id": "acc-1"}

    translated = _to_table_row(row)

    assert translated["status"] == "Живой"
    assert translated["last_checked"] == "никогда"
    assert translated["account_id"] == "acc-1"  # other fields preserved
    assert row["status"] == "alive"  # input not mutated


def test_to_table_row_keeps_real_last_checked() -> None:
    row: dict[str, object] = {"status": "new", "last_checked": "5m ago"}
    translated = _to_table_row(row)

    assert translated["status"] == "Новый"
    assert translated["last_checked"] == "5m ago"


def test_service_error_label_translates_exact_and_prefixed_messages() -> None:
    assert _service_error_label("Session file is empty") == "Файл сессии пустой"
    assert _service_error_label("tdata import failed: boom") == "Импорт tdata не удался: boom"
    assert (
        _service_error_label("Proxy not found for account: acc-1")
        == "Прокси не найден для аккаунта: acc-1"
    )
    assert _service_error_label("Session file is too large (5MB)").startswith(
        "Файл сессии слишком большой",
    )
    # Unmapped message passes through unchanged.
    assert _service_error_label("something else") == "something else"
