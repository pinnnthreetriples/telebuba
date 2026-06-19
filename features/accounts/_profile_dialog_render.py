"""Pure render helpers for the edit-profile dialog.

Owns the ``_DialogRefs`` bag plus every ``_render_*`` / ``_apply_snapshot``
function. Sibling of :mod:`_profile_dialog`, which keeps the tab-builder
closures and the entrypoint. Split out so neither file blows the aislop
file-size budget — the warming package follows the same precedent.
"""

from __future__ import annotations

import base64
import time
from typing import TYPE_CHECKING, Literal

from nicegui import app, ui

# TelegramMusicItem / TelegramStoryThumb are CONSTRUCTED at runtime in the
# optimistic-update helpers — keep them at module scope. AccountProfileSnapshot
# is annotation-only here (instances come in from the service layer) so it
# lives in TYPE_CHECKING.
from schemas.telegram_profile_snapshot import TelegramMusicItem, TelegramStoryThumb

if TYPE_CHECKING:
    import asyncio

    from schemas.accounts import AccountProfileSnapshot


_SECONDS_PER_MINUTE = 60
_MINUTES_PER_HOUR = 60

# Client ids whose websocket dropped. ``app.on_disconnect`` populates this once
# wired via ``register_disconnect_tracker()`` at app startup. Optimistic
# updates and ``_apply_snapshot`` consult it before mutating UI elements so
# they don't surface "Client has been deleted" warnings on detached clients.
# Multi-tab safe: each dialog tracks its own ``refs.client_id`` and the global
# set only flags the specific tab that died.
_DEAD_CLIENTS: set[str] = set()


def register_disconnect_tracker() -> None:
    """Wire ``app.on_disconnect`` once at startup to feed ``_DEAD_CLIENTS``."""

    def _on_disconnect(client: object) -> None:
        client_id = getattr(client, "id", None)
        if isinstance(client_id, str):
            _DEAD_CLIENTS.add(client_id)

    app.on_disconnect(_on_disconnect)


def _is_client_dead(refs: _DialogRefs) -> bool:
    return refs.closed or refs.client_id in _DEAD_CLIENTS


class _DialogRefs:
    """Element handles the background snapshot loader writes into.

    Attributes are wired up in ``_open_profile_dialog`` as the elements get
    created — declared here only so type checkers can see the shape.

    ``current_snapshot`` holds the latest applied snapshot so optimistic
    update helpers (``_apply_optimistic_*``) can mutate-and-rerender without
    a Telegram round-trip after each upload.

    ``client_id`` ties the dialog to the originating NiceGUI client so a
    global ``app.on_disconnect`` handler can flag this exact dialog dead
    without freezing other open tabs. ``closed`` is the same idea for the
    Quasar-side ``dialog.on('hide')`` event — flipped synchronously so the
    apply path can short-circuit before mutating detached elements.
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
    current_snapshot: AccountProfileSnapshot | None
    client_id: str
    closed: bool


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
    new_snapshot = refs.current_snapshot.model_copy(
        update={"avatar_bytes": image_bytes, "fetched_at_unix": time.time()},
    )
    refs.current_snapshot = new_snapshot
    _render_header(refs, new_snapshot)
    _render_photo_preview(refs.photo_preview_container, new_snapshot)


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
