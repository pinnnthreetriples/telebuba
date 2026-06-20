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


def render_photos_grid(refs: _DialogRefs, snapshot: AccountProfileSnapshot) -> None:
    """Render every profile photo as a swipeable carousel of slides.

    Earlier this was a 3-column grid — but with a real account's 10+ photos
    that pushed the upload widget below the dialog viewport, breaking the
    "add new photo" affordance. A carousel keeps one slide visible at a
    time (fixed height) so the upload form stays in sight. Newest photo is
    slide 0 and gets a "Текущая" badge; optimistic-add stubs render a
    disabled delete button because Telethon's ``InputPhoto`` refuses to
    identify them without a real ``file_reference``.
    """
    container = refs.photo_preview_container
    container.clear()
    with container:
        photos = snapshot.photos
        if not photos:
            ui.label("Фотографий в профиле нет").classes("text-sm text-grey-7")
            return
        ui.label(f"Всего фотографий: {len(photos)}").classes(
            "text-sm text-grey-7 q-mb-sm",
        )
        with (
            ui.carousel(value="0", arrows=True, navigation=True)
            .props("control-color=primary swipeable animated infinite=false")
            .classes("w-full bg-grey-2 rounded")
            .style("height: 360px")
        ):
            for index, photo in enumerate(photos):
                with ui.carousel_slide(name=str(index)).classes(
                    "column items-center justify-center p-3 gap-2",
                ):
                    _render_photo_card(refs, photo, is_current=index == 0)


def _render_photo_card(
    refs: _DialogRefs,
    photo: TelegramProfilePhoto,
    *,
    is_current: bool,
) -> None:
    thumb_url = _avatar_data_url(photo.thumb_bytes)
    deletable = photo.photo_id > 0 and bool(photo.file_reference)
    if thumb_url:
        ui.image(thumb_url).classes("max-h-56 object-contain rounded")
    else:
        ui.element("div").classes("w-32 h-32 bg-grey-3 rounded")
    with ui.row().classes("items-center gap-2"):
        if is_current:
            ui.badge("Текущая", color="primary")
        ui.label(_format_photo_date(photo.date_unix)).classes("text-xs text-grey-7")
        button = ui.button(
            icon="delete",
            color="grey-7",
            on_click=lambda _e=None, p=photo: _delete_photo(refs, p),
        ).props("flat dense round")
        if deletable:
            button.tooltip("Удалить эту фотографию")
        else:
            button.disable()
            button.tooltip("Сначала обновите данные кнопкой ↻ рядом с именем профиля")


def _format_photo_date(date_unix: int) -> str:
    if date_unix <= 0:
        return "Дата неизвестна"
    return datetime.fromtimestamp(date_unix, tz=UTC).strftime("%d.%m.%Y")


async def _delete_photo(refs: _DialogRefs, photo: TelegramProfilePhoto) -> None:
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
