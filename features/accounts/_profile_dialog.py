"""Edit-profile dialog with live Telegram pre-fill.

When the dialog opens the user immediately sees a header strip (avatar + name +
@username + phone) and the four edit tabs. A background task fetches the live
profile snapshot via :func:`services.accounts.fetch_live_account_profile` and
fills in the text inputs, the photo preview, the existing-stories grid, and the
existing-music list. A "↻" button re-fetches bypassing the 5-min TTL cache.

If Telegram refuses the fetch (FloodWait, RPCError) the dialog still opens and
shows the error inline — save/upload paths keep working against the local DB.
"""

from __future__ import annotations

import asyncio
import base64
import time
from typing import TYPE_CHECKING

from nicegui import ui

from features.accounts._table import _service_error_label
from schemas.accounts import AccountProfileSnapshot, AccountProfileUpdateRequest
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

    from schemas.telegram_profile_snapshot import TelegramMusicItem, TelegramStoryThumb


class _DialogRefs:
    """Element handles the background snapshot loader writes into.

    Attributes are wired up in :func:`_open_profile_dialog` as the elements
    get created — declared here only so type checkers can see the shape.
    """

    first_name: ui.input
    last_name: ui.input
    username: ui.input
    bio: ui.textarea
    avatar_slot: ui.element
    identity_slot: ui.element
    photo_preview_container: ui.element
    stories_container: ui.element
    music_section: ui.element
    music_list_container: ui.element
    sync_label: ui.label
    refresh_button: ui.button
    error_banner: ui.label
    initial_load_task: asyncio.Task[None]


def _avatar_data_url(image_bytes: bytes | None) -> str | None:
    if not image_bytes:
        return None
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


_SECONDS_PER_MINUTE = 60
_MINUTES_PER_HOUR = 60


def _humanize_ago(fetched_at_unix: float) -> str:
    if fetched_at_unix <= 0:
        return "—"
    delta = max(0, int(time.time() - fetched_at_unix))
    if delta < _SECONDS_PER_MINUTE:
        return "только что"
    minutes = delta // _SECONDS_PER_MINUTE
    if minutes < _MINUTES_PER_HOUR:
        return f"{minutes} мин назад"
    hours = minutes // _MINUTES_PER_HOUR
    return f"{hours} ч назад"


def _initials(first_name: str | None, last_name: str | None, account_id: str) -> str:
    for source in (first_name, last_name, account_id):
        if source:
            return source[0].upper()
    return "?"


def _render_header(refs: _DialogRefs, snapshot: AccountProfileSnapshot) -> None:
    """Refill the avatar + identity slots and stamp the sync timestamp."""
    refs.avatar_slot.clear()
    with refs.avatar_slot:
        avatar_url = _avatar_data_url(snapshot.avatar_bytes)
        if avatar_url:
            ui.image(avatar_url).classes(
                "w-14 h-14 rounded-full object-cover shrink-0",
            )
        else:
            ui.avatar(
                _initials(snapshot.first_name, snapshot.last_name, snapshot.account_id),
                color="primary",
            ).classes("shrink-0")

    refs.identity_slot.clear()
    with refs.identity_slot:
        display_name = (
            " ".join(
                filter(None, [snapshot.first_name, snapshot.last_name]),
            )
            or snapshot.account_id
        )
        ui.label(display_name).classes("text-base font-medium truncate")
        handle = f"@{snapshot.username}" if snapshot.username else "без юзернейма"
        phone = f" · {snapshot.phone}" if snapshot.phone else ""
        ui.label(f"{handle}{phone}").classes("text-xs text-grey-7 truncate")

    refs.sync_label.set_text(f"Обновлено {_humanize_ago(snapshot.fetched_at_unix)}")


def _render_photo_preview(
    container: ui.element,
    snapshot: AccountProfileSnapshot,
) -> None:
    container.clear()
    with container:
        avatar_url = _avatar_data_url(snapshot.avatar_bytes)
        if avatar_url:
            with ui.row().classes("items-center gap-3"):
                ui.image(avatar_url).classes(
                    "w-20 h-20 rounded-full object-cover",
                )
                ui.label("Текущая аватарка").classes("text-sm text-grey-7")
        else:
            ui.label("Аватарка не установлена").classes("text-sm text-grey-7")


def _render_stories_preview(
    container: ui.element,
    snapshot: AccountProfileSnapshot,
) -> None:
    container.clear()
    with container:
        if not snapshot.stories:
            ui.label("Закреплённых сторис нет").classes("text-sm text-grey-7")
            return
        ui.label(f"Закреплено сторис: {len(snapshot.stories)}").classes(
            "text-sm text-grey-7",
        )
        with (
            ui.scroll_area().classes("w-full h-32"),
            ui.row().classes("gap-2 no-wrap"),
        ):
            for story in snapshot.stories:
                _render_story_thumb(story)


def _render_story_thumb(story: TelegramStoryThumb) -> None:
    thumb_url = _avatar_data_url(story.thumb_bytes)
    cell = ui.element("div").classes(
        "w-20 h-28 rounded bg-grey-3 overflow-hidden relative shrink-0",
    )
    with cell:
        if thumb_url:
            ui.image(thumb_url).classes("w-full h-full object-cover")
        ui.label(story.kind).classes(
            "absolute bottom-0 left-0 right-0 text-center text-[10px] bg-black/40 text-white",
        )
        if story.caption:
            cell.tooltip(story.caption)


def _render_music_preview(
    refs: _DialogRefs,
    snapshot: AccountProfileSnapshot,
) -> None:
    # If the installed Telethon lacks the music TL methods, hide the whole tab
    # content above the upload form — a permanently-empty list is worse than
    # nothing.
    refs.music_section.set_visibility(snapshot.music_supported)
    refs.music_list_container.clear()
    if not snapshot.music_supported:
        return
    with refs.music_list_container:
        if not snapshot.music:
            ui.label("Музыка в профиле отсутствует").classes("text-sm text-grey-7")
            return
        with ui.list().props("dense bordered separator").classes("w-full"):
            for track in snapshot.music:
                with ui.item(), ui.item_section():
                    ui.item_label(track.title or "Без названия")
                    if track.performer or track.duration_seconds:
                        ui.item_label(_format_track_meta(track)).props("caption")


def _format_track_meta(track: TelegramMusicItem) -> str:
    parts: list[str] = []
    if track.performer:
        parts.append(track.performer)
    if track.duration_seconds:
        minutes = track.duration_seconds // _SECONDS_PER_MINUTE
        seconds = track.duration_seconds % _SECONDS_PER_MINUTE
        parts.append(f"{minutes}:{seconds:02d}")
    return " · ".join(parts)


def _apply_snapshot(refs: _DialogRefs, snapshot: AccountProfileSnapshot) -> None:
    """Fill every dynamic element from a freshly-loaded snapshot."""
    refs.first_name.value = snapshot.first_name or ""
    refs.last_name.value = snapshot.last_name or ""
    refs.username.value = snapshot.username or ""
    refs.bio.value = snapshot.bio or ""
    refs.first_name.enable()
    refs.last_name.enable()
    refs.username.enable()
    refs.bio.enable()
    refs.refresh_button.enable()

    if snapshot.error:
        refs.error_banner.set_text(f"Не удалось обновить: {snapshot.error}")
        refs.error_banner.set_visibility(True)
    else:
        refs.error_banner.set_visibility(False)

    _render_header(refs, snapshot)
    _render_photo_preview(refs.photo_preview_container, snapshot)
    _render_stories_preview(refs.stories_container, snapshot)
    _render_music_preview(refs, snapshot)


def _render_loading_header(refs: _DialogRefs, account_id: str) -> None:
    refs.avatar_slot.clear()
    with refs.avatar_slot:
        ui.spinner(size="md")
    refs.identity_slot.clear()
    with refs.identity_slot:
        ui.label(f"Загружаем профиль {account_id}…").classes("text-sm text-grey-7")
    refs.sync_label.set_text("обновляется…")


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
        await _load_and_apply(account_id, refs, force_refresh=True)

    ui.upload(
        label="Загрузить фото профиля",
        multiple=False,
        max_file_size=10_000_000,
        auto_upload=True,
        on_upload=handle_photo_upload,
        on_rejected=lambda _event: ui.notify("Фото профиля отклонено", type="warning"),
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
        await _load_and_apply(account_id, refs, force_refresh=True)

    ui.upload(
        label="Загрузить медиа для сторис",
        multiple=False,
        max_file_size=100_000_000,
        auto_upload=True,
        on_upload=handle_story_upload,
        on_rejected=lambda _event: ui.notify("Медиа для сторис отклонено", type="warning"),
    ).props('accept=".jpg,.jpeg,.png,.webp,.mp4,.mov"').classes("w-full")


def _profile_music_tab(account_id: str, refs: _DialogRefs) -> None:  # pragma: no cover
    refs.music_section = ui.column().classes("w-full gap-2")
    with refs.music_section:
        refs.music_list_container = ui.element("div").classes("w-full")

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
        await _load_and_apply(account_id, refs, force_refresh=True)

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
    refs = _DialogRefs()
    with (
        ui.dialog() as dialog,
        ui.column().classes(
            "bg-white p-4 gap-3 w-[640px] max-w-full",
        ),
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
    # Cancel the in-flight fetch if the user dismisses the dialog before it
    # returns — otherwise the task lands on detached NiceGUI elements and
    # surfaces noise in the server log. Task.cancel() is a no-op on a done
    # task, so wiring it unconditionally is safe.
    dialog.on("hide", refs.initial_load_task.cancel)
    dialog.open()
