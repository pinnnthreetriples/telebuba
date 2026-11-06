"""Link stickers: drawn into the story's media, and the tap areas that match them.

A ``MediaAreaUrl`` is only an invisible rectangle (layer 229 gives it no label), so the
visible plate is part of the picture. Every sticker is drawn on a transparent RGBA layer
the size of the canvas — a rounded plate, a link glyph, the label — rotated, and placed
at its centre. The ``MediaAreaCoordinates`` come from that same plate box, so a tap zone
always sits exactly on what the viewer sees.

Coordinates are percent of the media, ``x``/``y`` are the centre, ``rotation`` is
clockwise degrees and ``radius`` is the corner rounding as a percent of the media width
(Telegram's own conventions). Pillow rotates counter-clockwise, hence the sign flip.

A label no installed font can draw (an emoji, say) is refused with a stable code instead
of posting a plate with holes in it.
"""

from __future__ import annotations

import math
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Final, NamedTuple

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
from telethon.tl.types import MediaAreaCoordinates, MediaAreaUrl

if TYPE_CHECKING:
    from schemas.story_design import StoryLinkSticker

# The plate is this tall (percent of the canvas height) at scale 1.
PLATE_HEIGHT_PCT: Final = 6.0
_MAX_WIDTH_RATIO: Final = 0.94
_TEXT_RATIO: Final = 0.45
_PAD_RATIO: Final = 0.4
_ICON_RATIO: Final = 0.42
_GAP_RATIO: Final = 0.25
_SHADOW_RATIO: Final = 0.5
_SUPERSAMPLE: Final = 3
_JPEG_QUALITY: Final = 92

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
)

type _Rgba = tuple[int, int, int, int]


class _Style(NamedTuple):
    plate: _Rgba
    text: _Rgba
    shadow: bool


STYLES: Final[dict[str, _Style]] = {
    "white": _Style((255, 255, 255, 255), (17, 17, 17, 255), shadow=False),
    "dark": _Style((24, 24, 27, 255), (255, 255, 255, 255), shadow=False),
    # See-through white: the photo shows through, so the plate carries a soft shadow.
    "glass": _Style((255, 255, 255, 92), (255, 255, 255, 255), shadow=True),
    "accent": _Style((51, 144, 236, 255), (255, 255, 255, 255), shadow=False),
}
_SHADOW_ALPHA: Final = 70
_TEXT_SHADOW: Final[_Rgba] = (0, 0, 0, 90)


class StoryStickerError(ValueError):
    """A sticker cannot be drawn; ``str(exc)`` is the stable code the SPA translates."""

    def __init__(self) -> None:
        self.code = "story_link_label_unrenderable"
        super().__init__(self.code)


class _Plate(NamedTuple):
    font: ImageFont.FreeTypeFont
    width: int
    height: int
    text_width: float


def _font(text: str, size: int) -> ImageFont.FreeTypeFont | None:
    """The first font that has a glyph for every visible character of ``text``."""
    for candidate in _FONT_CANDIDATES:
        if not Path(candidate).is_file():
            continue
        font = ImageFont.truetype(candidate, size)
        missing = font.getmask("\uffff").getbbox()
        if all(font.getmask(char).getbbox() != missing for char in text if not char.isspace()):
            return font
    return None


def can_render_label(text: str) -> bool:
    """Whether some installed font can draw every character of ``text``."""
    return bool(text.strip()) and _font(text, 32) is not None


def _fit(label: str, height: float) -> _Plate:
    font = _font(label, max(1, round(height * _SUPERSAMPLE * _TEXT_RATIO)))
    if font is None:
        raise StoryStickerError
    text_width = font.getlength(label) / _SUPERSAMPLE
    # Whole pixels, so the tap area is exactly the box that gets drawn.
    width = math.ceil(height * (2 * _PAD_RATIO + _ICON_RATIO + _GAP_RATIO) + text_width)
    return _Plate(font, width, max(1, round(height)), text_width)


def _measure(label: str, height: float, canvas_width: int) -> _Plate:
    """The plate for ``label`` at ``height``, shrunk so it fits inside the canvas."""
    plate = _fit(label, height)
    limit = canvas_width * _MAX_WIDTH_RATIO
    return plate if plate.width <= limit else _fit(label, height * limit / plate.width)


def _link_glyph(big: int, colour: _Rgba) -> Image.Image:
    """Two interlocked rounded links, tilted like the usual chain icon (``big`` px square)."""
    icon = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(icon)
    stroke = max(1, round(big * 0.11))
    top, bottom = round(big * 0.3), round(big * 0.7)
    radius = round(big * 0.2)
    draw.rounded_rectangle(
        (round(big * 0.02), top, round(big * 0.62), bottom),
        radius=radius,
        outline=colour,
        width=stroke,
    )
    draw.rounded_rectangle(
        (round(big * 0.38), top, round(big * 0.98), bottom),
        radius=radius,
        outline=colour,
        width=stroke,
    )
    return icon.rotate(45, resample=Image.Resampling.BICUBIC)


def _plate_layer(plate: _Plate, sticker: StoryLinkSticker) -> Image.Image:
    """The unrotated sticker, with a margin around the plate for its shadow."""
    style = STYLES[sticker.style]
    margin = round(plate.height * _SHADOW_RATIO)
    width, height = round(plate.width), round(plate.height)
    big = _SUPERSAMPLE
    size = ((width + 2 * margin) * big, (height + 2 * margin) * big)
    box = (margin * big, margin * big, (margin + width) * big, (margin + height) * big)
    radius = height * big / 2
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=radius, fill=255)
    if style.shadow:
        shifted = ImageChops.offset(mask, 0, round(height * big * 0.08))
        blur = ImageFilter.GaussianBlur(height * big * 0.12)
        halo = ImageChops.subtract(shifted.filter(blur), mask).point(
            lambda value: value * _SHADOW_ALPHA // 255,
        )
        layer.paste((0, 0, 0, 255), mask=halo)
    layer.paste(style.plate, mask=mask)
    draw = ImageDraw.Draw(layer)
    icon_size = plate.height * _ICON_RATIO
    icon_x = margin + plate.height * _PAD_RATIO
    layer.alpha_composite(
        _link_glyph(round(icon_size * big), style.text),
        (round(icon_x * big), round((margin + (plate.height - icon_size) / 2) * big)),
    )
    text_at = (
        (icon_x + icon_size + plate.height * _GAP_RATIO) * big,
        (margin + plate.height / 2) * big,
    )
    if style.shadow:
        draw.text(
            (text_at[0], text_at[1] + height * big * 0.03),
            sticker.label,
            font=plate.font,
            fill=_TEXT_SHADOW,
            anchor="lm",
        )
    draw.text(text_at, sticker.label, font=plate.font, fill=style.text, anchor="lm")
    return layer.resize(
        (size[0] // big, size[1] // big),
        Image.Resampling.LANCZOS,
    )


def _clamped_centre(
    centre: tuple[float, float], plate: _Plate, rotation: float, canvas: tuple[int, int]
) -> tuple[float, float]:
    """Shift the centre so the rotated plate stays inside the canvas."""
    angle = math.radians(rotation)
    half_w = abs(plate.width / 2 * math.cos(angle)) + abs(plate.height / 2 * math.sin(angle))
    half_h = abs(plate.width / 2 * math.sin(angle)) + abs(plate.height / 2 * math.cos(angle))
    cx = min(max(centre[0], half_w), max(half_w, canvas[0] - half_w))
    cy = min(max(centre[1], half_h), max(half_h, canvas[1] - half_h))
    return cx, cy


def _composite(canvas: Image.Image, layer: Image.Image, centre: tuple[float, float]) -> None:
    """Alpha-composite ``layer`` centred at ``centre``, cropping what leaves the canvas."""
    left = round(centre[0] - layer.width / 2)
    top = round(centre[1] - layer.height / 2)
    crop_left, crop_top = max(0, -left), max(0, -top)
    crop_right = min(layer.width, canvas.width - left)
    crop_bottom = min(layer.height, canvas.height - top)
    if crop_right <= crop_left or crop_bottom <= crop_top:
        return
    piece = layer.crop((crop_left, crop_top, crop_right, crop_bottom))
    canvas.alpha_composite(piece, (left + crop_left, top + crop_top))


def render_stickers(
    stickers: list[StoryLinkSticker], size: tuple[int, int]
) -> tuple[Image.Image, list[MediaAreaUrl]]:
    """A transparent canvas-sized layer with every sticker drawn, and their tap areas."""
    width, height = size
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    areas: list[MediaAreaUrl] = []
    for sticker in stickers:
        plate = _measure(sticker.label, height * PLATE_HEIGHT_PCT / 100 * sticker.scale, width)
        centre = _clamped_centre(
            (sticker.x / 100 * width, sticker.y / 100 * height), plate, sticker.rotation, size
        )
        layer = _plate_layer(plate, sticker)
        if sticker.rotation:
            layer = layer.rotate(-sticker.rotation, resample=Image.Resampling.BICUBIC, expand=True)
        _composite(canvas, layer, centre)
        coordinates = MediaAreaCoordinates(
            x=centre[0] / width * 100,
            y=centre[1] / height * 100,
            w=plate.width / width * 100,
            h=plate.height / height * 100,
            rotation=float(sticker.rotation),
            radius=plate.height / 2 / width * 100,
        )
        areas.append(MediaAreaUrl(coordinates=coordinates, url=sticker.url))
    return canvas, areas


def stickers_on_photo(
    content: bytes, stickers: list[StoryLinkSticker]
) -> tuple[bytes, list[MediaAreaUrl]]:
    """The photo with every sticker drawn into it (re-encoded JPEG), and the tap areas."""
    with Image.open(BytesIO(content)) as opened:
        base = opened.convert("RGBA")
    layer, areas = render_stickers(stickers, base.size)
    out = BytesIO()
    Image.alpha_composite(base, layer).convert("RGB").save(
        out, format="JPEG", quality=_JPEG_QUALITY
    )
    return out.getvalue(), areas


def sticker_overlay_png(
    stickers: list[StoryLinkSticker], size: tuple[int, int]
) -> tuple[bytes, list[MediaAreaUrl]]:
    """The sticker layer as a PNG (for ffmpeg's overlay), and the tap areas."""
    layer, areas = render_stickers(stickers, size)
    out = BytesIO()
    layer.save(out, format="PNG")
    return out.getvalue(), areas
