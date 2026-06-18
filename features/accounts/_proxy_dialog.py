"""Proxy-settings dialog — UI form + tested label helpers.

Split out of ``features.accounts._dialogs`` to keep the parent module under
the aislop file-size gate. The ``_open_proxy_dialog`` body is UI
(``pragma: no cover``); the label/port helpers are pure and unit-tested.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from nicegui import ui

from features.accounts._table import _service_error_label
from schemas.proxy import AccountProxyCheckRequest, AccountProxyDelete, AccountProxyUpsert
from services.accounts import (
    check_account_proxy,
    delete_account_proxy,
    fetch_account_proxy_settings,
    save_account_proxy,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping


@dataclass
class _ProxyForm:
    proxy_type: ui.select
    host: ui.input
    port: ui.number
    username: ui.input
    password: ui.input


def _proxy_port_value(row: dict[str, object]) -> int:
    value = row.get("proxy_port")
    return value if isinstance(value, int) else 1080


def _proxy_dialog_status(row: Mapping[str, object]) -> str:
    status = str(row.get("proxy_status") or "unknown")
    labels = {
        "tcp_working": "Статус: работает",
        "failed": "Статус: ошибка",
        "unknown": "Статус: не проверен",
    }
    checked_at = str(row.get("proxy_last_checked_at") or "").strip()
    suffix = f" | проверено {checked_at}" if checked_at else ""
    return f"{labels.get(status, 'Статус: не проверен')}{suffix}"


def _proxy_dialog_geo(row: Mapping[str, object]) -> str:
    parts = [
        str(row.get("proxy_country_name") or "").strip(),
        str(row.get("proxy_country_code") or "").strip(),
        str(row.get("proxy_exit_ip") or "").strip(),
    ]
    value = " | ".join(part for part in parts if part)
    return f"Маршрут: {value}" if value else "Маршрут: страна/IP пока неизвестны"


def _proxy_dialog_error(row: Mapping[str, object]) -> str:
    error = str(row.get("proxy_last_error") or "").strip()
    return f"Ошибка: {error}" if error else ""


async def _save_proxy(  # pragma: no cover
    *,
    account_id: str,
    dialog: ui.dialog,
    refresh: Callable[[], Awaitable[None]],
    form: _ProxyForm,
) -> None:
    host_value = (form.host.value or "").strip()
    if not host_value:
        ui.notify("Хост прокси обязателен", type="warning")
        return
    try:
        await save_account_proxy(
            AccountProxyUpsert(
                account_id=account_id,
                proxy_type=form.proxy_type.value,
                host=host_value,
                port=int(form.port.value or 0),
                username=(form.username.value or "").strip() or None,
                password=(form.password.value or "").strip() or None,
            ),
        )
    except ValueError as exc:
        ui.notify(_service_error_label(str(exc)), type="negative")
        return
    dialog.close()
    ui.notify("Прокси сохранён", type="positive")
    await refresh()


async def _check_proxy(  # pragma: no cover
    *,
    account_id: str,
    refresh: Callable[[], Awaitable[None]],
    status_label: ui.label,
    geo_label: ui.label,
    error_label: ui.label,
) -> None:
    spinner = ui.notification(
        "Проверяем маршрут прокси...",
        spinner=True,
        timeout=None,
        close_button=False,
    )
    try:
        proxy = await check_account_proxy(AccountProxyCheckRequest(account_id=account_id))
    except ValueError as exc:
        ui.notify(_service_error_label(str(exc)), type="warning")
        return
    finally:
        spinner.dismiss()
    checked_row = {
        "proxy_status": proxy.status,
        "proxy_last_checked_at": proxy.last_checked_at,
        "proxy_last_error": proxy.last_error,
        "proxy_exit_ip": proxy.exit_ip,
        "proxy_country_code": proxy.country_code,
        "proxy_country_name": proxy.country_name,
    }
    status_label.set_text(_proxy_dialog_status(checked_row))
    geo_label.set_text(_proxy_dialog_geo(checked_row))
    error_label.set_text(_proxy_dialog_error(checked_row))
    ui.notify(
        "Прокси работает" if proxy.status == "tcp_working" else "Прокси не работает",
        type="positive" if proxy.status == "tcp_working" else "negative",
    )
    await refresh()


async def _remove_proxy(  # pragma: no cover
    *,
    account_id: str,
    dialog: ui.dialog,
    refresh: Callable[[], Awaitable[None]],
) -> None:
    await delete_account_proxy(AccountProxyDelete(account_id=account_id))
    dialog.close()
    ui.notify("Прокси удалён", type="positive")
    await refresh()


async def _open_proxy_dialog(  # pragma: no cover
    row: dict[str, object],
    refresh: Callable[[], Awaitable[None]],
) -> None:
    account_id = str(row["account_id"])
    existing = await fetch_account_proxy_settings(account_id)
    pw_hint = "оставьте пустым, чтобы не менять" if existing and existing.password else ""
    with ui.dialog() as dialog, ui.column().classes("bg-white p-4 gap-3 w-[460px] max-w-full"):
        ui.label("Настройки прокси").classes("text-base font-semibold")
        form = _ProxyForm(
            proxy_type=ui.select(
                ["socks5", "http"],
                value=str(row.get("proxy_type") or "socks5"),
                label="Тип",
            ).props("dense outlined"),
            host=ui.input("Хост", value=str(row.get("proxy_host") or "")).props("dense outlined"),
            port=ui.number("Порт", value=_proxy_port_value(row), min=1, max=65_535).props(
                "dense outlined",
            ),
            username=ui.input(
                "Логин",
                value=(existing.username if existing else None) or "",
            ).props("dense outlined clearable"),
            password=ui.input("Пароль", placeholder=pw_hint).props(
                "dense outlined clearable type=password",
            ),
        )
        with ui.column().classes(
            "w-full gap-1 rounded border border-slate-200 bg-slate-50 px-3 py-2",
        ):
            status_label = ui.label(_proxy_dialog_status(row)).classes("text-sm font-medium")
            geo_label = ui.label(_proxy_dialog_geo(row)).classes("text-xs text-slate-600")
            error_label = ui.label(_proxy_dialog_error(row)).classes("text-xs text-red-600")
        save = lambda: _save_proxy(  # noqa: E731 - inline thunk binds form refs for on_click.
            account_id=account_id,
            dialog=dialog,
            refresh=refresh,
            form=form,
        )
        check = lambda: _check_proxy(  # noqa: E731
            account_id=account_id,
            refresh=refresh,
            status_label=status_label,
            geo_label=geo_label,
            error_label=error_label,
        )
        remove = lambda: _remove_proxy(account_id=account_id, dialog=dialog, refresh=refresh)  # noqa: E731
        with ui.row().classes("w-full justify-between gap-2"):
            ui.button(icon="delete", color="negative", on_click=remove).tooltip("Удалить прокси")
            with ui.row().classes("gap-2"):
                ui.button(icon="travel_explore", color="primary", on_click=check).tooltip(
                    "Проверить прокси",
                )
                ui.button(icon="close", color="grey-7", on_click=dialog.close).tooltip("Отмена")
                ui.button(icon="save", color="primary", on_click=save).tooltip("Сохранить прокси")
    dialog.open()
