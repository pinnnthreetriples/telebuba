"""What a story carries besides its media: mention entities and a link area.

Pure helpers for ``_media_stories``. Entity offsets are counted in UTF-16 code units,
as Telegram counts them. A link area is only a clickable rectangle — layer 229 gives it
no label — so on a photo the plate under it is drawn into the picture, with the first
system font that has every glyph of the text; with none, the photo goes out unmarked.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from PIL import Image, ImageDraw, ImageFont

if TYPE_CHECKING:
    from schemas.telegram_actions_media import StoryMention

# The link plate, in percent of the 1080x1920 canvas: centred, near the bottom.
LINK_AREA = (50.0, 82.0, 56.0, 6.0)
_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
)
_PLATE_FILL = (255, 255, 255)
_TEXT_FILL = (17, 17, 17)
_JPEG_QUALITY = 92


class MentionSpan(NamedTuple):
    offset: int
    length: int
    user_id: int


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def caption_with_mentions(
    caption: str | None, mentions: list[StoryMention]
) -> tuple[str, list[MentionSpan]]:
    """The caption with every mention appended, and where each mention sits."""
    text = (caption or "").rstrip()
    spans: list[MentionSpan] = []
    for mention in mentions:
        label = f"@{mention.username}" if mention.username else (mention.first_name or "•")
        if text:
            text += " "
        spans.append(MentionSpan(_utf16_len(text), _utf16_len(label), mention.user_id))
        text += label
    return text, spans


def _font(text: str, size: int) -> ImageFont.FreeTypeFont | None:
    for candidate in _FONT_CANDIDATES:
        if not Path(candidate).is_file():
            continue
        font = ImageFont.truetype(candidate, size)
        missing = font.getmask("\uffff").getbbox()
        if all(font.getmask(char).getbbox() != missing for char in text if not char.isspace()):
            return font
    return None


def draw_link_plate(content: bytes, text: str) -> bytes:
    """Draw the link's plate where :data:`LINK_AREA` puts the clickable area."""
    with Image.open(BytesIO(content)) as opened:
        image = opened.convert("RGB")
    width, height = image.size
    x, y, w, h = LINK_AREA
    box = (
        (x - w / 2) / 100 * width,
        (y - h / 2) / 100 * height,
        (x + w / 2) / 100 * width,
        (y + h / 2) / 100 * height,
    )
    font = _font(text, int((box[3] - box[1]) * 0.45)) if text.strip() else None
    if font is None:
        return content
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(box, radius=int((box[3] - box[1]) / 2), fill=_PLATE_FILL)
    draw.text(
        ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2),
        text,
        font=font,
        fill=_TEXT_FILL,
        anchor="mm",
    )
    out = BytesIO()
    image.save(out, format="JPEG", quality=_JPEG_QUALITY)
    return out.getvalue()
