"""Which parts of a story design need Telegram Premium, and what a free account gets.

One classification for the immediate post and the scheduled one. A free account is
never refused for asking too much: the story goes out without the Premium parts and the
result names what was dropped. ``premium is None`` (never observed) sends the full story
first and falls back only if Telegram answers ``premium_required``.

Classification (everything else is available to every account):
- period other than 24 h  -> 24 h
- link stickers           -> dropped
- caption over the free limit -> cut at a word boundary with an ellipsis
- caption entities        -> dropped, the text is kept
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, NamedTuple

from core.config import settings
from schemas.story_design import StoryDesign, period_hours_for, utf16_length
from schemas.telegram_actions import PostStory
from services.accounts._result import AccountActionError, result_code

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from schemas.story_design import PremiumFeature
    from schemas.telegram_actions import ActionResult

PREMIUM_REQUIRED: Final = "premium_required"
_ELLIPSIS: Final = "…"
_FREE_PERIOD_HOURS: Final = 24


def _utf16_prefix(text: str, units: int) -> str:
    """The longest prefix of ``text`` that fits in ``units`` UTF-16 code units."""
    used = 0
    for index, char in enumerate(text):
        used += utf16_length(char)
        if used > units:
            return text[:index]
    return text


def _cut_caption(caption: str, limit: int) -> str:
    """``caption`` cut to ``limit`` UTF-16 units, ellipsis included, at a word boundary."""
    window = _utf16_prefix(caption, limit - 1)
    boundary = max((index for index, char in enumerate(window) if char.isspace()), default=0)
    head = window[:boundary] if boundary > 0 else window
    return head.rstrip() + _ELLIPSIS


def premium_policy(
    design: StoryDesign, free_caption_max: int
) -> tuple[StoryDesign, list[PremiumFeature]]:
    """The design a free account can post, and which Premium features it lost."""
    changes: dict[str, object] = {}
    lost: list[PremiumFeature] = []
    if design.period_hours != _FREE_PERIOD_HOURS:
        changes["period_hours"] = _FREE_PERIOD_HOURS
        lost.append("period")
    if design.link_stickers:
        changes["link_stickers"] = []
        lost.append("link_stickers")
    if design.caption is not None and utf16_length(design.caption) > free_caption_max:
        changes["caption"] = _cut_caption(design.caption, free_caption_max)
        lost.append("long_caption")
    if design.entities:
        changes["entities"] = []
        lost.append("formatting")
    return (design.model_copy(update=changes) if changes else design), lost


def free_design(design: StoryDesign) -> tuple[StoryDesign, list[PremiumFeature]]:
    return premium_policy(design, settings.profile_media.story_free_caption_max)


def flat_design(
    *,
    caption: str | None,
    privacy_preset: str,
    protect_content: bool,
    period_seconds: int,
) -> StoryDesign:
    """The design a pre-design (flat-field) request describes."""
    return StoryDesign.model_validate(
        {
            "caption": caption,
            "privacy_preset": privacy_preset,
            "protect_content": protect_content,
            "period_hours": period_hours_for(period_seconds),
        },
    )


def check_design_limits(design: StoryDesign) -> None:
    """Refuse a design past the configured sticker count or label length."""
    limits = settings.profile_media
    if len(design.link_stickers) > limits.story_link_stickers_max or any(
        len(sticker.label) > limits.story_link_label_max for sticker in design.link_stickers
    ):
        code = "story_design_invalid"
        raise AccountActionError(code)


class StoryMedia(NamedTuple):
    """The media half of a story post: what the design does not own."""

    filename: str
    content: bytes
    media_kind: str
    extra_images: list[bytes]
    collage_layout: str | None


def story_action(media: StoryMedia, design: StoryDesign) -> PostStory:
    return PostStory.model_validate(
        {
            "filename": media.filename,
            "content": media.content,
            "media_kind": media.media_kind,
            "extra_images": media.extra_images,
            "collage_layout": media.collage_layout,
            "caption": design.caption,
            "entities": design.entities,
            "link_stickers": design.link_stickers,
            "privacy_preset": design.privacy_preset,
            "period_seconds": design.period_seconds,
            "protect_content": design.protect_content,
            "pinned": design.pinned,
        },
    )


def with_design(action: PostStory, design: StoryDesign) -> PostStory:
    """``action`` with its design-owned fields replaced (the media is untouched)."""
    return action.model_copy(
        update={
            "caption": design.caption,
            "entities": design.entities,
            "link_stickers": design.link_stickers,
            "privacy_preset": design.privacy_preset,
            "period_seconds": design.period_seconds,
            "protect_content": design.protect_content,
            "pinned": design.pinned,
        },
    )


def refused_for_premium(result: ActionResult) -> bool:
    """Whether Telegram answered that the story needs Premium (a definite refusal)."""
    return result.status != "ok" and result_code(result) == PREMIUM_REQUIRED


async def send_story_for_account(
    send: Callable[[PostStory], Awaitable[ActionResult]],
    *,
    premium: bool | None,
    design: StoryDesign,
    make_action: Callable[[StoryDesign], PostStory],
) -> tuple[ActionResult, list[PremiumFeature]]:
    """Send ``design``, downgraded up front for a free account or once on a refusal.

    Free (``premium is False``): downgrade first. Premium or unknown: send in full, and
    when Telegram answers ``premium_required`` retry ONCE with the downgraded design —
    but only if the downgrade changes something, or the retry could not succeed either.
    The refusal is definite (nothing was posted), so the retry cannot double-post.
    """
    sent, lost = free_design(design) if premium is False else (design, [])
    result = await send(make_action(sent))
    if premium is not False and refused_for_premium(result):
        retry, dropped = free_design(design)
        if dropped:
            result = await send(make_action(retry))
            lost = dropped
    return result, lost
