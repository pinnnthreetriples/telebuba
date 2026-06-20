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
    """Render the account's stories as a compact row of poster-style cards.

    Replaced the fixed-height swipeable carousel: a 9:16 thumbnail looks
    narrow in a wide slide, and the per-slide metadata floated awkwardly
    beside the image. A horizontal row of small poster cards reads like
    the official Telegram story rail — each story is its own 112×192
    9:16 card with a "Активна"/"Закреплена" badge overlay, a tiny ✕
    delete button, and a date caption underneath. Many stories scroll
    horizontally; one story is just a single neat card without empty
    carousel chrome.
    """
    container = refs.stories_container
    container.clear()
    with container:
        stories = snapshot.stories
        if not stories:
            ui.label("Сторис на аккаунте нет").classes("text-sm text-grey-7")
            return
        with ui.row().classes("w-full no-wrap gap-3 overflow-x-auto q-pt-sm q-pb-xs"):
            for story in stories:
                _render_story_card(refs, story)
        ui.label(f"Всего сторис: {len(stories)}").classes(
            "text-xs text-grey-7 q-mt-xs",
        )


def _render_story_card(refs: _DialogRefs, story: TelegramStoryThumb) -> None:
    """Render one poster-style story card with overlay badge + delete button.

    Thumbnail fills the card (object-cover, no letterbox bars); badges and
    the trash button are absolutely positioned over the image so the card
    itself stays a clean 9:16 rectangle. Date + caption sit below the
    card as small captions.
    """
    thumb_url = _avatar_data_url(story.thumb_bytes)
    deletable = story.story_id > 0
    with ui.column().classes("items-center gap-1 shrink-0 w-28"):
        with ui.card().tight().classes("relative overflow-hidden rounded-lg"):
            if thumb_url:
                ui.image(thumb_url).classes("w-28 h-48 object-cover block")
            else:
                ui.element("div").classes("w-28 h-48 bg-grey-3")
            if story.is_active:
                # ``positive`` is Quasar's green; ``primary`` was reading as a
                # generic blue tag, but story rings on the official Telegram
                # client are green so the badge should match.
                ui.badge("Активна", color="positive").classes(
                    "absolute top-1 left-1",
                ).style("font-size: 9px")
            if story.is_pinned:
                ui.icon("push_pin", size="xs").classes(
                    "absolute top-1 right-1 text-white",
                ).style("filter: drop-shadow(0 0 2px rgba(0,0,0,0.6))")
            delete_btn = (
                ui.button(
                    icon="delete",
                    on_click=lambda _e=None, s=story: _delete_story(refs, s),
                )
                .props("dense round size=sm color=white")
                .classes(
                    "absolute bottom-1 right-1",
                )
                .style("background: rgba(0,0,0,0.6)")
            )
            if deletable:
                delete_btn.tooltip("Удалить эту сторис")
            else:
                delete_btn.disable()
                delete_btn.tooltip("Сначала обновите данные кнопкой ↻")
        ui.label(_format_story_date(story.date_unix)).classes(
            "text-[10px] text-grey-7",
        )
        if story.caption:
            # ``w-28`` on the column pins the caption width to the card so
            # ``truncate`` clips long copy with an ellipsis instead of pushing
            # the next card sideways.
            ui.label(story.caption).classes(
                "text-[10px] text-grey-8 truncate w-full text-center",
            ).tooltip(story.caption)


def _format_story_date(date_unix: int) -> str:
    # Short ``dd.mm HH:MM`` — full year would line-wrap the 112 px card
    # caption, and the year is rarely informative for stories with a
    # 24 h lifetime.
    if date_unix <= 0:
        return "—"
    return datetime.fromtimestamp(date_unix, tz=UTC).strftime("%d.%m %H:%M")


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
