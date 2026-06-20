"""Pure render helpers for the edit-profile dialog.

Owns the ``_DialogRefs`` bag plus every ``_render_*`` / ``_apply_snapshot``
function. Sibling of :mod:`_profile_dialog`, which keeps the tab-builder
closures and the entrypoint. Split out so neither file blows the aislop
file-size budget — the warming package follows the same precedent.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Literal

from nicegui import ui

from features.accounts._profile_dialog_common import (
    _DEAD_CLIENTS,
    _avatar_data_url,
    _DialogRefs,
    _is_client_dead,
    register_disconnect_tracker,
)
from features.accounts._profile_dialog_photos import render_photos_grid

# TelegramMusicItem / TelegramStoryThumb are CONSTRUCTED at runtime in the
# optimistic-update helpers — keep them at module scope. AccountProfileSnapshot
# is annotation-only here (instances come in from the service layer) so it
# lives in TYPE_CHECKING.
from schemas.profile_media import AccountProfileMusicRemove
from schemas.telegram_profile_snapshot import (
    TelegramMusicItem,
    TelegramProfilePhoto,
    TelegramStoryThumb,
)
from services.accounts import remove_account_profile_music

if TYPE_CHECKING:
    from schemas.accounts import AccountProfileSnapshot

# Re-export the common primitives so callers (main.py, tests) keep working
# off the historical ``_profile_dialog_render`` import path without churn.
__all__ = [
    "_DEAD_CLIENTS",
    "_DialogRefs",
    "_apply_optimistic_avatar",
    "_apply_optimistic_music",
    "_apply_optimistic_music_remove",
    "_apply_optimistic_story",
    "_apply_snapshot",
    "_avatar_data_url",
    "_is_client_dead",
    "_render_loading_header",
    "register_disconnect_tracker",
]

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
            " ".join(filter(None, [snapshot.first_name, snapshot.last_name])) or snapshot.account_id
        )
        ui.label(display_name).classes("text-base font-medium truncate")
        handle = f"@{snapshot.username}" if snapshot.username else "без юзернейма"
        phone = f" · {snapshot.phone}" if snapshot.phone else ""
        ui.label(f"{handle}{phone}").classes("text-xs text-grey-7 truncate")

    refs.sync_label.set_text(f"Обновлено {_humanize_ago(snapshot.fetched_at_unix)}")


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
                _render_music_row(refs, track)


def _render_music_row(refs: _DialogRefs, track: TelegramMusicItem) -> None:
    """Render one music row with title/meta on the left and ✕ delete on the right.

    Optimistic-add tracks (synthetic negative ``file_id`` or empty
    ``file_reference``) get a disabled delete button — Telethon's ``InputDocument``
    refuses to identify them without a real ``file_reference``. Hint the user
    to press ↻ to pull canonical metadata first.
    """
    deletable = track.file_id > 0 and bool(track.file_reference)
    with ui.item():
        with ui.item_section():
            ui.item_label(track.title or "Без названия")
            if track.performer or track.duration_seconds:
                ui.item_label(_format_track_meta(track)).props("caption")
        with ui.item_section().props("side"):
            button = ui.button(
                icon="close",
                color="grey-7",
                on_click=lambda _e=None, t=track: _delete_music_row(refs, t),
            ).props("flat dense round")
            if deletable:
                button.tooltip("Удалить трек из профиля")
            else:
                button.disable()
                button.tooltip("Сначала обновите данные кнопкой ↻ рядом с именем профиля")


async def _delete_music_row(refs: _DialogRefs, track: TelegramMusicItem) -> None:
    """Call the remove-music service, then optimistically drop the row."""
    try:
        await remove_account_profile_music(
            AccountProfileMusicRemove(
                account_id=refs.account_id,
                file_id=track.file_id,
                access_hash=track.access_hash,
                file_reference=track.file_reference,
            ),
        )
    except ValueError as exc:
        ui.notify(f"Не удалось удалить: {exc}", type="negative")
        return
    ui.notify("Трек удалён из профиля", type="positive")
    _apply_optimistic_music_remove(refs, track.file_id)


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
    if _is_client_dead(refs):
        # Dialog was hidden or websocket dropped while the fetch was in flight.
        # ``task.cancel()`` can't interrupt this synchronous block, so we
        # guard explicitly.
        return
    refs.current_snapshot = snapshot
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
    render_photos_grid(refs, snapshot)
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


def _apply_optimistic_avatar(refs: _DialogRefs, image_bytes: bytes) -> None:
    """Render a freshly-uploaded avatar without a Telegram round-trip.

    Telethon's ``UploadProfilePhotoRequest`` returns only file refs — bytes
    are not in the response. The user's local file bytes ARE on hand though,
    so we mutate the cached snapshot and re-render header + photo preview.
    The next forced refresh from Telegram (↻) re-syncs to the canonical
    server-side state.
    """
    if _is_client_dead(refs) or refs.current_snapshot is None:
        return
    optimistic_photo = TelegramProfilePhoto(
        photo_id=-int(time.time() * 1000),
        access_hash=0,
        file_reference=b"\x00",
        date_unix=int(time.time()),
        thumb_bytes=image_bytes,
    )
    new_snapshot = refs.current_snapshot.model_copy(
        update={
            "avatar_bytes": image_bytes,
            "photos": [optimistic_photo, *refs.current_snapshot.photos],
            "fetched_at_unix": time.time(),
        },
    )
    refs.current_snapshot = new_snapshot
    _render_header(refs, new_snapshot)
    render_photos_grid(refs, new_snapshot)


def _apply_optimistic_story(
    refs: _DialogRefs,
    *,
    story_bytes: bytes,
    kind: Literal["image", "video"],
    caption: str | None,
) -> None:
    """Append a freshly-posted story to the local preview list.

    We don't have Telegram's real ``story_id`` yet (would need to parse the
    ``Updates`` from ``SendStoryRequest``), so we use a negative synthetic
    id — the UI only uses it as a list key. Image bytes go directly as the
    thumb; video shows the placeholder until the next ↻ refresh pulls the
    server-generated thumbnail.
    """
    if _is_client_dead(refs) or refs.current_snapshot is None:
        return
    thumb_bytes = story_bytes if kind == "image" else None
    new_thumb = TelegramStoryThumb(
        story_id=-int(time.time() * 1000),
        kind=kind,
        caption=caption,
        thumb_bytes=thumb_bytes,
    )
    new_snapshot = refs.current_snapshot.model_copy(
        update={
            "stories": [*refs.current_snapshot.stories, new_thumb],
            "fetched_at_unix": time.time(),
        },
    )
    refs.current_snapshot = new_snapshot
    _render_stories_preview(refs.stories_container, new_snapshot)
    _render_header(refs, new_snapshot)


def _apply_optimistic_music(
    refs: _DialogRefs,
    *,
    title: str | None,
    performer: str | None,
    filename: str,
) -> None:
    """Append a freshly-saved track to the profile-music preview.

    ``SaveMusicRequest`` returns ``bool`` — no metadata to trust. We use the
    title/performer the user typed in the form, falling back to the filename
    stem when blank. Duration stays ``None`` until the next ↻ refresh pulls
    the real metadata.
    """
    if _is_client_dead(refs) or refs.current_snapshot is None:
        return
    new_track = TelegramMusicItem(
        file_id=-int(time.time() * 1000),
        title=title or filename,
        performer=performer,
        duration_seconds=None,
    )
    new_snapshot = refs.current_snapshot.model_copy(
        update={
            "music": [*refs.current_snapshot.music, new_track],
            "fetched_at_unix": time.time(),
        },
    )
    refs.current_snapshot = new_snapshot
    _render_music_preview(refs, new_snapshot)
    _render_header(refs, new_snapshot)


def _apply_optimistic_music_remove(refs: _DialogRefs, file_id: int) -> None:
    """Drop a track from the local music preview after a successful unsave."""
    if _is_client_dead(refs) or refs.current_snapshot is None:
        return
    remaining = [t for t in refs.current_snapshot.music if t.file_id != file_id]
    new_snapshot = refs.current_snapshot.model_copy(
        update={"music": remaining, "fetched_at_unix": time.time()},
    )
    refs.current_snapshot = new_snapshot
    _render_music_preview(refs, new_snapshot)
    _render_header(refs, new_snapshot)
