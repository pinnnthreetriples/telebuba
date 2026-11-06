"""What a story's caption carries: formatting entities and appended mentions.

Pure helpers for ``_media_stories``. Entity offsets are counted in UTF-16 code units,
as Telegram counts them. Link stickers live in ``_story_stickers``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, NamedTuple

from telethon.tl.types import (
    MessageEntityBold,
    MessageEntityCode,
    MessageEntityItalic,
    MessageEntitySpoiler,
    MessageEntityStrike,
    MessageEntityTextUrl,
    MessageEntityUnderline,
)

from schemas.story_design import utf16_length

if TYPE_CHECKING:
    from telethon.tl.types import TypeMessageEntity

    from schemas.story_design import StoryEntity
    from schemas.telegram_actions_media import StoryMention

# The masstagging link plate: centred, near the bottom, as tall as a sticker at scale 1.
LINK_STICKER_CENTRE = (50.0, 82.0)
_PLAIN_ENTITIES: Final = {
    "bold": MessageEntityBold,
    "italic": MessageEntityItalic,
    "underline": MessageEntityUnderline,
    "strike": MessageEntityStrike,
    "spoiler": MessageEntitySpoiler,
    "code": MessageEntityCode,
}


class MentionSpan(NamedTuple):
    offset: int
    length: int
    user_id: int


def caption_with_mentions(
    caption: str | None, mentions: list[StoryMention]
) -> tuple[str, list[MentionSpan]]:
    """The caption (trimmed both ends) with every mention appended, and where each sits."""
    text = (caption or "").strip()
    spans: list[MentionSpan] = []
    for mention in mentions:
        label = f"@{mention.username}" if mention.username else (mention.first_name or "•")
        if text:
            text += " "
        spans.append(MentionSpan(utf16_length(text), utf16_length(label), mention.user_id))
        text += label
    return text, spans


def caption_entities(entities: list[StoryEntity], caption: str | None) -> list[TypeMessageEntity]:
    """The operator's formatting as Telegram entities, clipped to the caption as sent.

    The caption loses leading and trailing whitespace on its way out (as
    :func:`caption_with_mentions` sends it), so every entity shifts left by the trimmed
    head and is clipped to what is left: shortened, or dropped when nothing of it
    remains. Mentions are appended after the caption, so they never move these offsets.
    """
    raw = caption or ""
    head = utf16_length(raw) - utf16_length(raw.lstrip())
    text_length = utf16_length(raw.strip())
    mapped: list[TypeMessageEntity] = []
    for entity in entities:
        start = max(entity.offset - head, 0)
        end = min(entity.offset + entity.length - head, text_length)
        length = end - start
        if length < 1:
            continue
        if entity.type == "text_url":
            mapped.append(MessageEntityTextUrl(start, length, entity.url or ""))
        else:
            mapped.append(_PLAIN_ENTITIES[entity.type](start, length))
    return mapped
