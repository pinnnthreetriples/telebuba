"""Edit-profile dialog entrypoint + per-tab builders.

When the dialog opens the user immediately sees a header strip (avatar + name +
@username + phone) and the four edit tabs. A background task fetches the live
profile snapshot via :func:`services.accounts.fetch_live_account_profile` and
fills in the text inputs, the photo preview, the existing-stories grid, and the
existing-music list. A "↻" button re-fetches bypassing the 5-min TTL cache.

If Telegram refuses the fetch (FloodWait, RPCError) the dialog still opens and
shows the error inline — save/upload paths keep working against the local DB.

Render helpers + ``_DialogRefs`` live in :mod:`_profile_dialog_render` so
neither file blows the aislop size budget.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from nicegui import context, ui

from features.accounts._profile_dialog_render import (
    _apply_optimistic_avatar,
    _apply_optimistic_music,
    _apply_optimistic_story,
    _apply_snapshot,
    _DialogRefs,
    _render_loading_header,
)
from features.accounts._table import _service_error_label
from schemas.accounts import AccountProfileUpdateRequest
from schemas.profile_media import (
    AccountProfileMusicUpload,
    AccountProfilePhotoUpload,
    AccountStoryUpload,
)
from services.accounts import (
    add_account_profile_music,
    fetch_live_account_profile,
    post_account_story,
    set_account_profile_photo,
    update_account_profile,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from nicegui.events import UploadEventArguments


async def _load_and_apply(
    account_id: str,
    refs: _DialogRefs,
    *,
    force_refresh: bool,
) -> None:
    refs.refresh_button.disable()
    _render_loading_header(refs, account_id)
    snapshot = await fetch_live_account_profile(account_id, force_refresh=force_refresh)
    _apply_snapshot(refs, snapshot)


def _profile_text_tab(
    account_id: str,
    refs: _DialogRefs,
    refresh: Callable[[], Awaitable[None]],
    close: Callable[..., object],
) -> None:  # pragma: no cover
    refs.first_name = ui.input("Имя", value="").props("dense outlined").classes("w-full")
    refs.last_name = (
        ui.input("Фамилия", value="").props("dense outlined clearable").classes("w-full")
    )
    refs.username = (
        ui.input("Юзернейм", value="").props("dense outlined clearable prefix=@").classes("w-full")
    )
    refs.bio = ui.textarea("Описание", value="").props("dense outlined").classes("w-full")
    refs.first_name.disable()
    refs.last_name.disable()
    refs.username.disable()
    refs.bio.disable()

    async def save() -> None:
        name = (refs.first_name.value or "").strip()
        if not name:
            ui.notify("Имя обязательно", type="warning")
            return
        try:
            await update_account_profile(
                AccountProfileUpdateRequest(
                    account_id=account_id,
                    first_name=name,
                    last_name=(refs.last_name.value or "").strip(),
                    username=(refs.username.value or "").strip().removeprefix("@"),
                    bio=(refs.bio.value or "").strip(),
                ),
            )
        except ValueError as exc:
            ui.notify(_service_error_label(str(exc)), type="negative")
            return
        ui.notify("Профиль обновлён", type="positive")
        await refresh()
        await _load_and_apply(account_id, refs, force_refresh=True)

    with ui.row().classes("w-full justify-end gap-2"):
        ui.button(icon="close", color="grey-7", on_click=close).tooltip("Отмена")
        ui.button(icon="save", color="primary", on_click=save).tooltip("Сохранить профиль")


def _profile_photo_tab(account_id: str, refs: _DialogRefs) -> None:  # pragma: no cover
    refs.photo_preview_container = ui.element("div").classes("w-full")

    async def handle_photo_upload(event: UploadEventArguments) -> None:
        content = await event.file.read()
        try:
            await set_account_profile_photo(
                AccountProfilePhotoUpload(
                    account_id=account_id,
                    filename=event.file.name,
                    content=content,
                ),
            )
        except ValueError as exc:
            ui.notify(_service_error_label(str(exc)), type="negative")
            return
        ui.notify("Фото профиля обновлено", type="positive")
        # Optimistic UI: the bytes we just uploaded ARE the new avatar.
        # Skips the post-write Telegram re-fetch that previously raced the
        # websocket heartbeat dead.
        _apply_optimistic_avatar(refs, content)

    ui.upload(
        label="Загрузить фото профиля",
        multiple=False,
        max_file_size=10_000_000,
        auto_upload=True,
        on_upload=handle_photo_upload,
        on_rejected=lambda _e: ui.notify(
            "Фото отклонено. Проверь: размер ≤ 10 МБ, формат — JPG/JPEG/PNG/WebP.",
            type="warning",
            timeout=8000,
        ),
    ).props('accept=".jpg,.jpeg,.png,.webp"').classes("w-full")


def _profile_story_tab(account_id: str, refs: _DialogRefs) -> None:  # pragma: no cover
    refs.stories_container = ui.element("div").classes("w-full")

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
        content = await event.file.read()
        caption = (story_caption.value or "").strip() or None
        kind = story_kind.value
        try:
            await post_account_story(
                AccountStoryUpload(
                    account_id=account_id,
                    filename=event.file.name,
                    content=content,
                    media_kind=kind,
                    caption=caption,
                    privacy_preset=story_privacy.value,
                    protect_content=bool(protect_story.value),
                ),
            )
        except ValueError as exc:
            ui.notify(_service_error_label(str(exc)), type="negative")
            return
        ui.notify("Сторис опубликована", type="positive")
        _apply_optimistic_story(refs, story_bytes=content, kind=kind, caption=caption)

    ui.upload(
        label="Загрузить медиа для сторис",
        multiple=False,
        max_file_size=100_000_000,
        auto_upload=True,
        on_upload=handle_story_upload,
        on_rejected=lambda _e: ui.notify(
            "Медиа отклонено. Проверь: размер ≤ 100 МБ, формат — JPG/JPEG/PNG/WebP/MP4/MOV.",
            type="warning",
            timeout=8000,
        ),
    ).props('accept=".jpg,.jpeg,.png,.webp,.mp4,.mov"').classes("w-full")


def _profile_music_tab(account_id: str, refs: _DialogRefs) -> None:  # pragma: no cover
    refs.music_section = ui.column().classes("w-full gap-2")
    with refs.music_section:
        refs.music_list_container = ui.element("div").classes("w-full")

    music_title = ui.input("Название").props("dense outlined clearable")
    music_performer = ui.input("Исполнитель").props("dense outlined clearable")

    async def handle_music_upload(event: UploadEventArguments) -> None:
        title = (music_title.value or "").strip() or None
        performer = (music_performer.value or "").strip() or None
        try:
            await add_account_profile_music(
                AccountProfileMusicUpload(
                    account_id=account_id,
                    filename=event.file.name,
                    content=await event.file.read(),
                    title=title,
                    performer=performer,
                ),
            )
        except ValueError as exc:
            ui.notify(_service_error_label(str(exc)), type="negative")
            return
        ui.notify("Музыка профиля добавлена", type="positive")
        _apply_optimistic_music(
            refs,
            title=title,
            performer=performer,
            filename=event.file.name,
        )

    ui.upload(
        label="Загрузить музыку",
        multiple=False,
        max_file_size=30_000_000,
        auto_upload=True,
        on_upload=handle_music_upload,
        on_rejected=lambda _e: ui.notify(
            "Музыка отклонена. Проверь: размер ≤ 30 МБ, формат — MP3 или M4A.",
            type="warning",
            timeout=8000,
        ),
    ).props('accept=".mp3,.m4a"').classes("w-full")


async def _open_profile_dialog(
    row: dict[str, object],
    refresh: Callable[[], Awaitable[None]],
) -> None:  # pragma: no cover
    account_id = str(row["account_id"])
    refs = _DialogRefs()
    refs.account_id = account_id
    refs.current_snapshot = None
    refs.client_id = context.client.id
    refs.closed = False
    with (
        ui.dialog() as dialog,
        ui.column().classes("bg-white p-4 gap-3 w-[640px] max-w-full"),
    ):
        ui.label("Редактировать профиль").classes("text-base font-semibold")
        with ui.row().classes(
            "items-center gap-3 w-full no-wrap border rounded-md p-3 bg-grey-1",
        ):
            refs.avatar_slot = ui.element("div").classes("shrink-0")
            refs.identity_slot = ui.column().classes("gap-0 flex-1 min-w-0")
            with ui.column().classes("items-end gap-1 shrink-0"):
                refs.refresh_button = (
                    ui.button(
                        icon="refresh",
                        color="grey-7",
                        on_click=lambda: _load_and_apply(
                            account_id,
                            refs,
                            force_refresh=True,
                        ),
                    )
                    .props("flat dense round")
                    .tooltip("Обновить с Telegram")
                )
                refs.refresh_button.disable()
                refs.sync_label = ui.label("—").classes("text-[10px] text-grey-6")
        refs.error_banner = ui.label("").classes(
            "text-xs text-negative bg-red-1 rounded px-2 py-1",
        )
        refs.error_banner.set_visibility(False)

        with ui.tabs().classes("w-full") as tabs:
            text_tab = ui.tab("Текст")
            photo_tab = ui.tab("Фото")
            story_tab = ui.tab("Сторис")
            music_tab = ui.tab("Музыка")
        with ui.tab_panels(tabs, value=text_tab).classes("w-full"):
            with ui.tab_panel(text_tab).classes("gap-3"):
                _profile_text_tab(account_id, refs, refresh, dialog.close)
            with ui.tab_panel(photo_tab).classes("gap-3"):
                _profile_photo_tab(account_id, refs)
            with ui.tab_panel(story_tab).classes("gap-3"):
                _profile_story_tab(account_id, refs)
            with ui.tab_panel(music_tab).classes("gap-3"):
                _profile_music_tab(account_id, refs)

    _render_loading_header(refs, account_id)
    refs.initial_load_task = asyncio.create_task(
        _load_and_apply(account_id, refs, force_refresh=False),
    )

    def _on_hide() -> None:
        # Cancel the in-flight fetch AND flip the closed flag so apply paths
        # short-circuit when they land after dialog hide. ``cancel()`` alone
        # is not enough — the apply path has no await points after the fetch,
        # so cancellation can't interrupt it.
        refs.closed = True
        refs.initial_load_task.cancel()

    dialog.on("hide", _on_hide)
    dialog.open()
