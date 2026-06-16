"""Accounts dialogs — add / edit-profile / proxy, plus their tested helpers.

The ``_open_*`` builders and per-tab helpers are UI (``pragma: no cover``); the
proxy label/port helpers are pure and unit-tested.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import ui

from features.accounts._table import _service_error_label
from schemas.accounts import (
    AccountCheckRequest,
    AccountProfileUpdateRequest,
    AccountSessionFileImport,
)
from schemas.profile_media import (
    AccountProfileMusicUpload,
    AccountProfilePhotoUpload,
    AccountStoryUpload,
)
from schemas.proxy import AccountProxyCheckRequest, AccountProxyDelete, AccountProxyUpsert
from schemas.tdata import TdataConvertRequest
from services.accounts import (
    add_account_profile_music,
    check_account_proxy,
    check_account_session,
    delete_account_proxy,
    import_account_session,
    import_account_tdata,
    post_account_story,
    save_account_proxy,
    set_account_profile_photo,
    update_account_profile,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from nicegui.events import UploadEventArguments


async def _check_accounts(account_ids: set[str]) -> None:  # pragma: no cover
    if not account_ids:
        ui.notify("Аккаунты не выбраны", type="warning")
        return
    for account_id in sorted(account_ids):
        await check_account_session(AccountCheckRequest(account_id=account_id))
    ui.notify("Проверка сессий завершена", type="positive")


async def _open_add_dialog(refresh: Callable[[], Awaitable[None]]) -> None:  # pragma: no cover
    with ui.dialog() as dialog, ui.column().classes("bg-white p-4 gap-3 w-96 max-w-full"):
        ui.label("Добавить аккаунт").classes("text-base font-semibold")
        label = ui.input("Отображаемое имя").props("dense outlined")

        async def handle_session_upload(event: UploadEventArguments) -> None:
            try:
                await import_account_session(
                    AccountSessionFileImport(
                        filename=event.file.name,
                        content=await event.file.read(),
                        label=label.value or None,
                    ),
                )
            except ValueError as exc:
                ui.notify(_service_error_label(str(exc)), type="warning")
                return
            dialog.close()
            await refresh()

        async def handle_tdata_upload(event: UploadEventArguments) -> None:
            try:
                accounts = await import_account_tdata(
                    TdataConvertRequest(
                        filename=event.file.name,
                        content=await event.file.read(),
                        label=label.value or None,
                    ),
                )
            except ValueError as exc:
                ui.notify(_service_error_label(str(exc)), type="warning")
                return
            dialog.close()
            ui.notify(f"Импортировано аккаунтов из tdata: {len(accounts)}", type="positive")
            await refresh()

        ui.upload(
            label="Загрузить .session",
            multiple=False,
            max_file_size=20_000_000,
            auto_upload=True,
            on_upload=handle_session_upload,
            on_rejected=lambda _event: ui.notify("Файл сессии отклонён", type="warning"),
        ).props('accept=".session"').classes("w-full")

        ui.label("или").classes("self-center text-xs text-slate-500")

        ui.upload(
            label="Загрузить tdata.zip",
            multiple=False,
            # Telegram Desktop tdata can be hundreds of MB with cached emoji
            # and user_data — generous cap to fit a real archive.
            max_file_size=1_000_000_000,
            auto_upload=True,
            on_upload=handle_tdata_upload,
            on_rejected=lambda _event: ui.notify(
                "Архив tdata отклонён: нужен .zip до 1 ГБ",
                type="warning",
            ),
        ).props('accept=".zip"').classes("w-full")

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button(icon="close", color="grey-7", on_click=dialog.close).tooltip("Отмена")
    dialog.open()


def _profile_text_tab(
    account_id: str,
    refresh: Callable[[], Awaitable[None]],
    close: Callable[..., object],
) -> None:  # pragma: no cover
    first_name = ui.input("Имя", value="").props("dense outlined")
    last_name = ui.input("Фамилия", value="").props("dense outlined clearable")
    username = ui.input("Юзернейм", value="").props("dense outlined clearable prefix=@")
    bio = ui.textarea("Описание", value="").props("dense outlined")

    async def save() -> None:
        name = (first_name.value or "").strip()
        if not name:
            ui.notify("Имя обязательно", type="warning")
            return
        try:
            await update_account_profile(
                AccountProfileUpdateRequest(
                    account_id=account_id,
                    first_name=name,
                    last_name=(last_name.value or "").strip(),
                    username=(username.value or "").strip().removeprefix("@"),
                    bio=(bio.value or "").strip(),
                ),
            )
        except ValueError as exc:
            ui.notify(_service_error_label(str(exc)), type="negative")
            return
        ui.notify("Профиль обновлён", type="positive")
        await refresh()

    with ui.row().classes("w-full justify-end gap-2"):
        ui.button(icon="close", color="grey-7", on_click=close).tooltip("Отмена")
        ui.button(icon="save", color="primary", on_click=save).tooltip("Сохранить профиль")


def _profile_photo_tab(account_id: str) -> None:  # pragma: no cover
    async def handle_photo_upload(event: UploadEventArguments) -> None:
        try:
            await set_account_profile_photo(
                AccountProfilePhotoUpload(
                    account_id=account_id,
                    filename=event.file.name,
                    content=await event.file.read(),
                ),
            )
        except ValueError as exc:
            ui.notify(_service_error_label(str(exc)), type="negative")
            return
        ui.notify("Фото профиля обновлено", type="positive")

    ui.upload(
        label="Загрузить фото профиля",
        multiple=False,
        max_file_size=10_000_000,
        auto_upload=True,
        on_upload=handle_photo_upload,
        on_rejected=lambda _event: ui.notify("Фото профиля отклонено", type="warning"),
    ).props('accept=".jpg,.jpeg,.png,.webp"').classes("w-full")


def _profile_story_tab(account_id: str) -> None:  # pragma: no cover
    story_kind = ui.select(
        {"image": "Изображение", "video": "Видео"},
        value="image",
        label="Медиа",
    ).props("dense outlined")
    story_privacy = ui.select(
        {
            "contacts": "Контакты",
            "close_friends": "Близкие друзья",
            "public": "Публично",
        },
        value="contacts",
        label="Приватность",
    ).props("dense outlined")
    story_caption = ui.textarea("Подпись").props("dense outlined")
    protect_story = ui.checkbox("Защитить контент", value=False)

    async def handle_story_upload(event: UploadEventArguments) -> None:
        try:
            await post_account_story(
                AccountStoryUpload(
                    account_id=account_id,
                    filename=event.file.name,
                    content=await event.file.read(),
                    media_kind=story_kind.value,
                    caption=(story_caption.value or "").strip() or None,
                    privacy_preset=story_privacy.value,
                    protect_content=bool(protect_story.value),
                ),
            )
        except ValueError as exc:
            ui.notify(_service_error_label(str(exc)), type="negative")
            return
        ui.notify("Сторис опубликована", type="positive")

    ui.upload(
        label="Загрузить медиа для сторис",
        multiple=False,
        max_file_size=100_000_000,
        auto_upload=True,
        on_upload=handle_story_upload,
        on_rejected=lambda _event: ui.notify("Медиа для сторис отклонено", type="warning"),
    ).props('accept=".jpg,.jpeg,.png,.webp,.mp4,.mov"').classes("w-full")


def _profile_music_tab(account_id: str) -> None:  # pragma: no cover
    music_title = ui.input("Название").props("dense outlined clearable")
    music_performer = ui.input("Исполнитель").props("dense outlined clearable")

    async def handle_music_upload(event: UploadEventArguments) -> None:
        try:
            await add_account_profile_music(
                AccountProfileMusicUpload(
                    account_id=account_id,
                    filename=event.file.name,
                    content=await event.file.read(),
                    title=(music_title.value or "").strip() or None,
                    performer=(music_performer.value or "").strip() or None,
                ),
            )
        except ValueError as exc:
            ui.notify(_service_error_label(str(exc)), type="negative")
            return
        ui.notify("Музыка профиля добавлена", type="positive")

    ui.upload(
        label="Загрузить музыку",
        multiple=False,
        max_file_size=30_000_000,
        auto_upload=True,
        on_upload=handle_music_upload,
        on_rejected=lambda _event: ui.notify("Музыка отклонена", type="warning"),
    ).props('accept=".mp3,.m4a"').classes("w-full")


async def _open_profile_dialog(
    row: dict[str, object],
    refresh: Callable[[], Awaitable[None]],
) -> None:  # pragma: no cover
    account_id = str(row["account_id"])
    with ui.dialog() as dialog, ui.column().classes("bg-white p-4 gap-3 w-[560px] max-w-full"):
        ui.label("Редактировать профиль").classes("text-base font-semibold")
        with ui.tabs().classes("w-full") as tabs:
            text_tab = ui.tab("Текст")
            photo_tab = ui.tab("Фото")
            story_tab = ui.tab("Сторис")
            music_tab = ui.tab("Музыка")
        with ui.tab_panels(tabs, value=text_tab).classes("w-full"):
            with ui.tab_panel(text_tab).classes("gap-3"):
                _profile_text_tab(account_id, refresh, dialog.close)
            with ui.tab_panel(photo_tab).classes("gap-3"):
                _profile_photo_tab(account_id)
            with ui.tab_panel(story_tab).classes("gap-3"):
                _profile_story_tab(account_id)
            with ui.tab_panel(music_tab).classes("gap-3"):
                _profile_music_tab(account_id)
    dialog.open()


async def _open_proxy_dialog(
    row: dict[str, object],
    refresh: Callable[[], Awaitable[None]],
) -> None:  # pragma: no cover
    account_id = str(row["account_id"])
    with ui.dialog() as dialog, ui.column().classes("bg-white p-4 gap-3 w-[460px] max-w-full"):
        ui.label("Настройки прокси").classes("text-base font-semibold")
        proxy_type = ui.select(
            ["socks5", "http"],
            value=str(row.get("proxy_type") or "socks5"),
            label="Тип",
        ).props("dense outlined")
        host = ui.input("Хост", value=str(row.get("proxy_host") or "")).props("dense outlined")
        port = ui.number("Порт", value=_proxy_port_value(row), min=1, max=65_535).props(
            "dense outlined",
        )
        username = ui.input("Логин").props("dense outlined clearable")
        password = ui.input("Пароль").props("dense outlined clearable type=password")
        with ui.column().classes(
            "w-full gap-1 rounded border border-slate-200 bg-slate-50 px-3 py-2",
        ):
            status_label = ui.label(_proxy_dialog_status(row)).classes("text-sm font-medium")
            geo_label = ui.label(_proxy_dialog_geo(row)).classes("text-xs text-slate-600")
            error_label = ui.label(_proxy_dialog_error(row)).classes("text-xs text-red-600")

        async def save() -> None:
            host_value = (host.value or "").strip()
            if not host_value:
                ui.notify("Хост прокси обязателен", type="warning")
                return
            try:
                await save_account_proxy(
                    AccountProxyUpsert(
                        account_id=account_id,
                        proxy_type=proxy_type.value,
                        host=host_value,
                        port=int(port.value or 0),
                        username=(username.value or "").strip() or None,
                        password=(password.value or "").strip() or None,
                    ),
                )
            except ValueError as exc:
                ui.notify(_service_error_label(str(exc)), type="negative")
                return
            dialog.close()
            ui.notify("Прокси сохранён", type="positive")
            await refresh()

        async def check_proxy() -> None:
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

        async def remove() -> None:
            await delete_account_proxy(AccountProxyDelete(account_id=account_id))
            dialog.close()
            ui.notify("Прокси удалён", type="positive")
            await refresh()

        with ui.row().classes("w-full justify-between gap-2"):
            ui.button(icon="delete", color="negative", on_click=remove).tooltip("Удалить прокси")
            with ui.row().classes("gap-2"):
                ui.button(icon="travel_explore", color="primary", on_click=check_proxy).tooltip(
                    "Проверить прокси",
                )
                ui.button(icon="close", color="grey-7", on_click=dialog.close).tooltip("Отмена")
                ui.button(icon="save", color="primary", on_click=save).tooltip("Сохранить прокси")
    dialog.open()


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
