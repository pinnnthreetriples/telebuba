"""Link-sticker rendering: what is drawn, and the tap area that must sit exactly on it."""

from __future__ import annotations

import math
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageFont

from core.telegram_client import _story_stickers as stickers
from core.telegram_client._story_stickers import (
    StoryStickerError,
    can_render_label,
    render_stickers,
    sticker_overlay_png,
    stickers_on_photo,
)
from schemas.story_design import StoryLinkSticker

_CANVAS = (1080, 1920)
_LONG_LABEL = "abcdefghijklmnopqrstuvwxyz0123"


@pytest.fixture(autouse=True)
def _bundled_font(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pillow's own font, so the geometry tests do not depend on the machine's fonts."""
    monkeypatch.setattr(
        stickers, "_font", lambda _text, size: ImageFont.load_default(max(size, 1)), raising=True
    )


def _sticker(**fields: Any) -> StoryLinkSticker:
    values: dict[str, Any] = {"url": "https://example.com", "label": "Go", "x": 50, "y": 50}
    return StoryLinkSticker.model_validate({**values, **fields})


def _alpha_at(layer: Image.Image, point: tuple[int, int]) -> int:
    value = layer.getchannel("A").getpixel(point)
    assert isinstance(value, int)
    return value


def _rgba_at(layer: Image.Image, point: tuple[int, int]) -> tuple[int, ...]:
    value = layer.getpixel(point)
    assert isinstance(value, tuple)
    return value


def _alpha_box(layer: Image.Image) -> tuple[int, int, int, int]:
    """The box of solid pixels (the resampling's faint fringe is not part of the plate)."""
    box = layer.getchannel("A").point(lambda value: 255 if value > 127 else 0).getbbox()
    assert box is not None
    return box


def test_the_tap_area_is_the_drawn_plate() -> None:
    layer, [area] = render_stickers([_sticker(label=_LONG_LABEL)], _CANVAS)
    coords = area.coordinates

    left = (coords.x - coords.w / 2) / 100 * _CANVAS[0]
    right = (coords.x + coords.w / 2) / 100 * _CANVAS[0]
    top = (coords.y - coords.h / 2) / 100 * _CANVAS[1]
    bottom = (coords.y + coords.h / 2) / 100 * _CANVAS[1]
    # White has no shadow, so the opaque pixels ARE the plate.
    assert _alpha_box(layer) == pytest.approx((left, top, right, bottom), abs=1.5)
    assert area.url == "https://example.com"
    assert (coords.x, coords.y, coords.rotation) == (50, 50, 0)


def test_the_corner_radius_is_a_percent_of_the_media_width() -> None:
    _, [area] = render_stickers([_sticker()], _CANVAS)
    coords = area.coordinates

    assert coords.radius == pytest.approx(coords.h / 100 * _CANVAS[1] / 2 / _CANVAS[0] * 100)


def test_the_plate_is_six_percent_tall_and_scales() -> None:
    _, [plain, big] = render_stickers([_sticker(), _sticker(scale=2.0, y=20)], _CANVAS)

    assert plain.coordinates.h == pytest.approx(6.0, abs=0.05)
    assert big.coordinates.h == pytest.approx(12.0, abs=0.05)
    assert big.coordinates.w > plain.coordinates.w


def test_a_very_long_label_is_shrunk_to_fit_the_canvas() -> None:
    _, [area] = render_stickers([_sticker(label="W" * 40, scale=2.5)], _CANVAS)

    assert area.coordinates.w <= 94.5
    assert area.coordinates.h < 15


def test_the_plate_is_drawn_where_the_area_says_in_a_photo() -> None:
    grey = BytesIO()
    Image.new("RGB", _CANVAS, (128, 128, 128)).save(grey, format="JPEG")

    out, [area] = stickers_on_photo(grey.getvalue(), [_sticker(label=_LONG_LABEL)])

    coords = area.coordinates
    left = (coords.x - coords.w / 2) / 100 * _CANVAS[0]
    mid_y = round(coords.y / 100 * _CANVAS[1])
    with Image.open(BytesIO(out)) as image:
        assert image.size == _CANVAS
        inside = image.getpixel((round(left + coords.h / 100 * _CANVAS[1] * 0.2), mid_y))
        outside = image.getpixel((round(left - 12), mid_y))
    assert inside == pytest.approx((255, 255, 255), abs=6)
    assert outside == pytest.approx((128, 128, 128), abs=6)


def test_rotation_is_clockwise() -> None:
    layer, [area] = render_stickers([_sticker(label=_LONG_LABEL, rotation=45)], _CANVAS)
    coords = area.coordinates
    centre_x, centre_y = coords.x / 100 * _CANVAS[0], coords.y / 100 * _CANVAS[1]
    width, height = coords.w / 100 * _CANVAS[0], coords.h / 100 * _CANVAS[1]
    reach = width / 2 - height * 0.6
    assert reach * math.sin(math.radians(45)) > height  # the check below is not vacuous

    along = (
        round(centre_x + reach * math.cos(math.radians(45))),
        round(centre_y + reach * math.sin(math.radians(45))),
    )
    mirrored = (along[0], round(centre_y - reach * math.sin(math.radians(45))))
    # Screen y grows downwards: clockwise turns the right end DOWN.
    assert _alpha_at(layer, along) > 200
    assert _alpha_at(layer, mirrored) == 0
    assert coords.rotation == 45


def test_a_counter_clockwise_rotation_is_reported_negative() -> None:
    layer, [area] = render_stickers([_sticker(label=_LONG_LABEL, rotation=-45)], _CANVAS)
    coords = area.coordinates
    centre_x, centre_y = coords.x / 100 * _CANVAS[0], coords.y / 100 * _CANVAS[1]
    reach = coords.w / 100 * _CANVAS[0] / 2 - coords.h / 100 * _CANVAS[1] * 0.6
    up = (
        round(centre_x + reach * math.cos(math.radians(45))),
        round(centre_y - reach * math.sin(math.radians(45))),
    )

    assert _alpha_at(layer, up) > 200
    assert coords.rotation == -45


@pytest.mark.parametrize(
    ("x", "y", "rotation"),
    [(0, 0, 0), (100, 100, 0), (0, 100, 30), (100, 0, -120), (50, 0, 90)],
)
def test_a_sticker_is_kept_inside_the_canvas(x: float, y: float, rotation: float) -> None:
    layer, [area] = render_stickers([_sticker(x=x, y=y, rotation=rotation, label="Go")], _CANVAS)

    left, top, right, bottom = _alpha_box(layer)
    assert left >= 0
    assert top >= 0
    assert right <= _CANVAS[0]
    assert bottom <= _CANVAS[1]
    # The area follows the shifted centre, not the requested one.
    coords = area.coordinates
    assert coords.x == pytest.approx((left + right) / 2 / _CANVAS[0] * 100, abs=1.0)
    assert coords.y == pytest.approx((top + bottom) / 2 / _CANVAS[1] * 100, abs=1.0)


@pytest.mark.parametrize("style", ["white", "dark", "accent"])
def test_a_solid_style_paints_its_plate_colour(style: str) -> None:
    layer, [area] = render_stickers([_sticker(style=style, label=_LONG_LABEL)], _CANVAS)
    coords = area.coordinates
    left = (coords.x - coords.w / 2) / 100 * _CANVAS[0]
    pad_pixel = (round(left + coords.h / 100 * _CANVAS[1] * 0.2), round(coords.y / 100 * 1920))

    assert layer.getpixel(pad_pixel) == stickers.STYLES[style].plate


def test_glass_is_see_through_and_casts_a_shadow() -> None:
    layer, [area] = render_stickers([_sticker(style="glass", label=_LONG_LABEL)], _CANVAS)
    coords = area.coordinates
    left = (coords.x - coords.w / 2) / 100 * _CANVAS[0]
    pad_pixel = (round(left + coords.h / 100 * _CANVAS[1] * 0.15), round(coords.y / 100 * 1920))

    assert _rgba_at(layer, pad_pixel)[3] == pytest.approx(92, abs=12)
    # The shadow spills past the plate.
    box = layer.getchannel("A").getbbox()
    assert box is not None
    plate_h = coords.h / 100 * _CANVAS[1]
    assert box[3] - box[1] > plate_h + 2


def test_the_overlay_png_is_a_transparent_canvas_with_the_same_areas() -> None:
    png, areas = sticker_overlay_png([_sticker(label="Go", rotation=10)], (720, 1280))
    _, photo_areas = render_stickers([_sticker(label="Go", rotation=10)], (1080, 1920))

    with Image.open(BytesIO(png)) as overlay:
        assert overlay.size == (720, 1280)
        assert overlay.mode == "RGBA"
        assert _rgba_at(overlay, (0, 0))[3] == 0
        assert overlay.getchannel("A").getbbox() is not None
    # Percent coordinates: the same story, whatever the canvas.
    video, photo = areas[0].coordinates, photo_areas[0].coordinates
    assert video.w == pytest.approx(photo.w, rel=0.1)
    assert video.h == pytest.approx(photo.h, rel=0.05)
    assert (video.x, video.y) == (photo.x, photo.y)


def test_no_font_means_a_stable_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()  # drop the bundled-font fixture: use the real lookup
    monkeypatch.setattr(stickers, "_FONT_CANDIDATES", ())

    with pytest.raises(StoryStickerError) as excinfo:
        render_stickers([_sticker(label="Go")], _CANVAS)

    assert str(excinfo.value) == "story_link_label_unrenderable"
    assert excinfo.value.code == "story_link_label_unrenderable"
    assert not can_render_label("Go")
    assert not can_render_label("   ")


def test_a_label_no_font_can_draw_is_refused_not_posted_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.undo()
    if not any(Path(candidate).is_file() for candidate in stickers._FONT_CANDIDATES):
        pytest.skip("no system font to test the missing-glyph check against")

    assert can_render_label("Go")
    assert not can_render_label("\U0001f517")
    with pytest.raises(StoryStickerError):
        render_stickers([_sticker(label="\U0001f517 link")], _CANVAS)
