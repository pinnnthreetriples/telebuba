"""Pure render helpers for the edit-profile dialog.

Owns the ``_DialogRefs`` bag plus every ``_render_*`` / ``_apply_snapshot``
function. Sibling of :mod:`_profile_dialog`, which keeps the tab-builder
closures and the entrypoint. Split out so neither file blows the aislop
file-size budget — the warming package follows the same precedent.
"""

from __future__ import annotations

import base64
import time
from typing import TYPE_CHECKING

from nicegui import ui

if TYPE_CHECKING:
    import asyncio

    from schemas.accounts import AccountProfileSnapshot
    from schemas.telegram_profile_snapshot import TelegramMusicItem, TelegramStoryThumb


_SECONDS_PER_MINUTE = 60
_MINUTES_PER_HOUR = 60


class _DialogRefs:
    """Element handles the background snapshot loader writes into.

    Attributes are wired up in ``_open_profile_dialog`` as the elements get
    created — declared here only so type checkers can see the shape.
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
            " ".join(filter(None, [snapshot.first_name, snapshot.last_name])) or snapshot.account_id
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
                ui.image(avatar_url).classes("w-20 h-20 rounded-full object-cover")
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


def _render_music_preview(refs: _DialogRefs, snapshot: AccountProfileSnapshot) -> None:
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
