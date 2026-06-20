"""Profile-photos carousel: slides, dates, per-photo delete + optimistic helpers.

Split out of :mod:`_profile_dialog_render` so neither file blows the aislop
size budget (the same precedent set when the warming engine was split). The
render module imports helpers from here via a local import inside
``_apply_snapshot`` / ``_apply_optimistic_avatar`` to keep the dependency
one-directional and avoid an import cycle.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from nicegui import ui

from features.accounts._profile_dialog_common import (
    _avatar_data_url,
    _DialogRefs,
    _is_client_dead,
)
from schemas.profile_media import AccountProfilePhotoRemove
from services.accounts import remove_account_profile_photo

if TYPE_CHECKING:
    from schemas.accounts import AccountProfileSnapshot
    from schemas.telegram_profile_snapshot import TelegramProfilePhoto


def render_photos_grid(  # pragma: no cover - NiceGUI render path, exercised in browser
    refs: _DialogRefs,
    snapshot: AccountProfileSnapshot,
) -> None:
    """Render every profile photo as a horizontal row of poster-style cards.

    Same UX pattern as the stories rail (square 96 px tiles, badge overlay,
    overlay delete) — keeps the four edit tabs visually consistent. Newest
    photo is leftmost and gets a green "Текущая" badge; optimistic-add stubs
    render with a disabled delete button because Telethon's ``InputPhoto``
    refuses to identify them without a real ``file_reference``.
    """
    container = refs.photo_preview_container
    container.clear()
    with container:
        photos = snapshot.photos
        if not photos:
            ui.label("Фотографий в профиле нет").classes("text-sm text-grey-7")
            return
        with ui.row().classes("w-full no-wrap gap-3 overflow-x-auto q-pt-sm q-pb-xs"):
            for index, photo in enumerate(photos):
                _render_photo_card(refs, photo, is_current=index == 0)
        ui.label(f"Всего фотографий: {len(photos)}").classes(
            "text-xs text-grey-7 q-mt-xs",
        )


def _render_photo_card(  # pragma: no cover - NiceGUI render path
    refs: _DialogRefs,
    photo: TelegramProfilePhoto,
    *,
    is_current: bool,
) -> None:
    """Render one square poster card with overlay badge + delete button.

    Profile photos are typically square / portrait, so the card is a clean
    112 px square with ``object-cover`` — matches the stories rail style.
    """
    thumb_url = _avatar_data_url(photo.thumb_bytes)
    deletable = photo.photo_id > 0 and bool(photo.file_reference)
    with ui.column().classes("items-center gap-1 shrink-0 w-28"):
        with ui.card().tight().classes("relative overflow-hidden rounded-lg"):
            if thumb_url:
                ui.image(thumb_url).classes("w-28 h-28 object-cover block")
            else:
                ui.element("div").classes("w-28 h-28 bg-grey-3")
            if is_current:
                # ``positive`` (green) mirrors the active-story badge in the
                # stories rail — the operator scans both tabs the same way.
                ui.badge("Текущая", color="positive").classes(
                    "absolute top-1 left-1",
                ).style("font-size: 9px")
            delete_btn = (
                ui.button(
                    icon="delete",
                    on_click=lambda _e=None, p=photo: _delete_photo(refs, p),
                )
                .props("dense round size=sm color=negative")
                .classes("absolute bottom-1 right-1")
            )
            if deletable:
                delete_btn.tooltip("Удалить эту фотографию")
            else:
                delete_btn.disable()
                delete_btn.tooltip("Сначала обновите данные кнопкой ↻")
        ui.label(_format_photo_date(photo.date_unix)).classes(
            "text-[10px] text-grey-7",
        )


def _format_photo_date(date_unix: int) -> str:
    if date_unix <= 0:
        return "Дата неизвестна"
    return datetime.fromtimestamp(date_unix, tz=UTC).strftime("%d.%m.%Y")


async def _delete_photo(  # pragma: no cover - NiceGUI click handler
    refs: _DialogRefs,
    photo: TelegramProfilePhoto,
) -> None:
    try:
        await remove_account_profile_photo(
            AccountProfilePhotoRemove(
                account_id=refs.account_id,
                photo_id=photo.photo_id,
                access_hash=photo.access_hash,
                file_reference=photo.file_reference,
            ),
        )
    except ValueError as exc:
        ui.notify(f"Не удалось удалить: {exc}", type="negative")
        return
    ui.notify("Фотография удалена", type="positive")
    apply_optimistic_photo_remove(refs, photo.photo_id)


def apply_optimistic_photo_remove(refs: _DialogRefs, photo_id: int) -> None:
    """Drop a photo from the local grid + bump the avatar to the next one.

    Telegram auto-promotes the previous photo to current when the active
    avatar is deleted — mirror that in the cached snapshot so the header
    refreshes without a round-trip. ``avatar_bytes`` follows the first
    remaining photo's thumb; the next ↻ refresh re-syncs to the canonical
    server-side state. ``_render_header`` is imported lazily because the
    render module already imports this module at top level — keeping that
    second-leg import inside the function breaks the cycle without resorting
    to a passed-in callback.
    """
    from features.accounts._profile_dialog_render import (  # noqa: PLC0415 — cycle break
        _render_header,
    )

    if _is_client_dead(refs) or refs.current_snapshot is None:
        return
    snapshot = refs.current_snapshot
    remaining = [p for p in snapshot.photos if p.photo_id != photo_id]
    new_avatar_bytes = snapshot.avatar_bytes
    was_current = bool(snapshot.photos) and snapshot.photos[0].photo_id == photo_id
    if was_current:
        new_avatar_bytes = remaining[0].thumb_bytes if remaining else None
    new_snapshot = snapshot.model_copy(
        update={
            "photos": remaining,
            "avatar_bytes": new_avatar_bytes,
            "fetched_at_unix": time.time(),
        },
    )
    refs.current_snapshot = new_snapshot
    render_photos_grid(refs, new_snapshot)
    _render_header(refs, new_snapshot)
