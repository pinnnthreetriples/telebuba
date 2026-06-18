"""Edit-profile dialog and its per-tab upload handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import ui

from features.accounts._table import _service_error_label
from schemas.accounts import AccountProfileUpdateRequest
from schemas.profile_media import (
    AccountProfileMusicUpload,
    AccountProfilePhotoUpload,
    AccountStoryUpload,
)
from services.accounts import (
    add_account_profile_music,
    post_account_story,
    set_account_profile_photo,
    update_account_profile,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from nicegui.events import UploadEventArguments


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
