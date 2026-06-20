"""Profile-stories carousel: slides, badges, per-story delete + optimistic helpers.

Mirrors :mod:`_profile_dialog_photos` so the operator interacts with the
account's currently-visible stories (active 24 h ring + profile-pinned) the
same way as with the profile-photo history. Live stories may be both
active and pinned; the badges read independently so a single slide can
carry both labels.

The render module imports ``render_stories_carousel`` at module load — to
keep the dependency one-directional, this module's optimistic-remove
imports ``_render_header`` lazily (the same pattern the photos module uses).
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
from schemas.profile_media import AccountStoryRemove
from services.accounts import remove_account_story

if TYPE_CHECKING:
    from schemas.accounts import AccountProfileSnapshot
    from schemas.telegram_profile_snapshot import TelegramStoryThumb


def render_stories_carousel(refs: _DialogRefs, snapshot: AccountProfileSnapshot) -> None:
    """Render the account's active + pinned stories as a compact carousel.

    Sized small enough that the upload form stays visible inside the dialog
    viewport. Slides are already sorted newest-first by the service layer;
    the carousel starts on slide 0 so the latest story is the first thing
    the operator sees. Quasar's bullet ``navigation`` overlaps content
    visually inside a fixed-height slide — we keep the arrows for paging
    and drop the dots; the total-count line below doubles as a position
    cue ("3 / 13" style is overkill for typical 1–5 stories).
    """
    container = refs.stories_container
    container.clear()
    with container:
        stories = snapshot.stories
        if not stories:
            ui.label("Сторис на аккаунте нет").classes("text-sm text-grey-7")
            return
        with (
            ui.carousel(value="0", arrows=True, navigation=False)
            .props("control-color=primary swipeable animated infinite=false")
            .classes("w-full bg-grey-2 rounded")
            .style("height: 260px")
        ):
            for index, story in enumerate(stories):
                with ui.carousel_slide(name=str(index)).classes(
                    "column items-center justify-center p-2 gap-1",
                ):
                    _render_story_slide(refs, story)
        ui.label(f"Всего сторис: {len(stories)}").classes(
            "text-xs text-grey-7 q-mt-xs",
        )


def _render_story_slide(refs: _DialogRefs, story: TelegramStoryThumb) -> None:
    thumb_url = _avatar_data_url(story.thumb_bytes)
    deletable = story.story_id > 0
    if thumb_url:
        # Without navigation dots the slide gets ~36 px more vertical room;
        # raise the image cap so the preview is bigger and reads sharper.
        ui.image(thumb_url).classes("max-h-52 object-contain rounded")
    else:
        ui.element("div").classes("w-24 h-24 bg-grey-3 rounded")
    with ui.row().classes("items-center gap-1"):
        if story.is_active:
            ui.badge("Активна", color="primary")
        if story.is_pinned:
            ui.badge("Закреплена", color="secondary")
        ui.label(_format_story_date(story.date_unix)).classes("text-xs text-grey-7")
        button = ui.button(
            icon="delete",
            color="grey-7",
            on_click=lambda _e=None, s=story: _delete_story(refs, s),
        ).props("flat dense round")
        if deletable:
            button.tooltip("Удалить эту сторис")
        else:
            button.disable()
            button.tooltip("Сначала обновите данные кнопкой ↻")
    if story.caption:
        ui.label(story.caption).classes("text-xs text-grey-8 text-center truncate")


def _format_story_date(date_unix: int) -> str:
    if date_unix <= 0:
        return "Дата неизвестна"
    return datetime.fromtimestamp(date_unix, tz=UTC).strftime("%d.%m.%Y %H:%M")


async def _delete_story(refs: _DialogRefs, story: TelegramStoryThumb) -> None:
    try:
        await remove_account_story(
            AccountStoryRemove(account_id=refs.account_id, story_id=story.story_id),
        )
    except ValueError as exc:
        ui.notify(f"Не удалось удалить: {exc}", type="negative")
        return
    ui.notify("Сторис удалена", type="positive")
    apply_optimistic_story_remove(refs, story.story_id)


def apply_optimistic_story_remove(refs: _DialogRefs, story_id: int) -> None:
    """Drop one story from the local snapshot and re-render the carousel.

    Unlike photo removal we don't need to repaint the header — the avatar
    isn't tied to the stories list. ``_render_header`` is imported lazily
    only so we can stamp ``fetched_at_unix`` and bounce the "обновлено"
    timestamp; if the import ever breaks, the carousel still updates.
    """
    from features.accounts._profile_dialog_render import (  # noqa: PLC0415 — cycle break
        _render_header,
    )

    if _is_client_dead(refs) or refs.current_snapshot is None:
        return
    snapshot = refs.current_snapshot
    remaining = [s for s in snapshot.stories if s.story_id != story_id]
    new_snapshot = snapshot.model_copy(
        update={"stories": remaining, "fetched_at_unix": time.time()},
    )
    refs.current_snapshot = new_snapshot
    render_stories_carousel(refs, new_snapshot)
    _render_header(refs, new_snapshot)
