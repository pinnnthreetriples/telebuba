"""Pure-Pillow story-image composition — single-photo canvas + multi-photo collage.

No Telethon here: this module only turns raw upload bytes into a 1080x1920 JPEG
ready for ``stories.sendStory``. The gateway (``_media.py``) calls into it.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageFilter, UnidentifiedImageError

from core.config import settings


class StoryImageNormalisationError(ValueError):
    """Raised when a story image can't be decoded onto the 1080x1920 canvas.

    Mirrors :class:`core.telegram_client._video.StoryVideoNormalisationError`:
    ``str(exc)`` is the stable, locale-neutral code — it survives the
    ``execute`` → ``ActionResult.error_message`` → API error-envelope path as a
    code the SPA translates, never Russian prose (non-negotiable #12).
    """

    def __init__(self) -> None:
        self.code = "story_image_invalid"
        super().__init__(self.code)


class StoryCollageLayoutError(ValueError):
    """Raised when a collage's requested layout id can't be resolved.

    Same contract as :class:`StoryImageNormalisationError`: ``str(exc)`` is the
    stable, locale-neutral code the SPA translates. The unresolvable detail
    (bad id / unsupported count) rides the chained cause into the failure log.
    """

    def __init__(self) -> None:
        self.code = "story_collage_unknown_layout"
        super().__init__(self.code)


def _decode_story_source(content: bytes) -> Image.Image:
    """Decode arbitrary upload bytes into an RGB Pillow image, or raise.

    Shared by the single-photo and collage paths so both surface the same
    locale-neutral ``story_image_invalid`` code for undecodable input.
    """
    try:
        with Image.open(BytesIO(content)) as opened:
            opened.load()
            return opened.convert("RGB") if opened.mode != "RGB" else opened.copy()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        # UnidentifiedImageError = container Pillow can't decode (e.g. HEIC/JXL
        # renamed to .png); OSError from load() = truncated/corrupt bytes. The
        # chained cause carries the Pillow reason plus the file's real magic
        # bytes so the telegram_post_story_failed log shows what the file was.
        detail = f"{type(exc).__name__}: {exc}; magic={content[:12].hex()}"
        raise StoryImageNormalisationError from ValueError(detail)


def _normalize_story_image_for_telegram(content: bytes) -> bytes:
    """Compose a photo onto Telegram's 1080x1920 story canvas (JPEG q90).

    The source is fitted into the canvas without cropping; the empty
    margins are filled with a heavily-blurred enlarged copy of the same
    photo, matching how the official Telegram Android client composes
    stories (StoryEntry.java: ``backgroundFile`` is a blurred upscale of
    the source). Solid-color letterbox is functionally accepted by the
    server but looks visibly cheaper than the official UX. Anything
    outside the 9:16 aspect window gets rejected with
    ``PHOTO_INVALID_DIMENSIONS``, so this step is required, not optional.
    """
    target_width, target_height = 1080, 1920
    source = _decode_story_source(content)
    canvas = _blurred_story_background(source, target_width, target_height)
    fitted = source.copy()
    fitted.thumbnail((target_width, target_height), Image.Resampling.LANCZOS)
    canvas.paste(
        fitted,
        (
            (target_width - fitted.width) // 2,
            (target_height - fitted.height) // 2,
        ),
    )
    output = BytesIO()
    canvas.save(output, format="JPEG", quality=90)
    return output.getvalue()


def _cover_crop(source: Image.Image, width: int, height: int) -> Image.Image:
    """Scale-and-center-crop ``source`` to exactly ``width`` x ``height`` (no bars)."""
    scale = max(width / source.width, height / source.height)
    scaled = source.resize(
        (max(int(source.width * scale), width), max(int(source.height * scale), height)),
        Image.Resampling.LANCZOS,
    )
    left = (scaled.width - width) // 2
    top = (scaled.height - height) // 2
    return scaled.crop((left, top, left + width, top + height))


def _blurred_story_background(
    source: Image.Image,
    target_width: int,
    target_height: int,
) -> Image.Image:
    """Render the blurred-cover fill that goes behind the fitted source.

    Cover-crops the source to the full 1080x1920 canvas, then applies a strong
    Gaussian blur so the edges read as ambient colour rather than a recognisable
    second copy of the image. Mirrors the official Android client's story
    background composition.
    """
    cover = _cover_crop(source, target_width, target_height)
    return cover.filter(ImageFilter.GaussianBlur(radius=50))


# Collage layout templates: photo count → layout id → list of cells, each a
# ``(x, y, w, h)`` rect in fractions of the 1080x1920 canvas. The first layout
# for a count is that count's default when the client omits ``collage_layout``.
# The number of source images must equal the number of cells (guaranteed by
# keying on count; enforced by ``zip(strict=True)`` in the composer).
_THIRD = 1 / 3
_COLLAGE_TEMPLATES: dict[int, dict[str, list[tuple[float, float, float, float]]]] = {
    2: {
        "v2": [(0, 0, 1, 0.5), (0, 0.5, 1, 0.5)],
        "h2": [(0, 0, 0.5, 1), (0.5, 0, 0.5, 1)],
    },
    3: {
        "v3": [(0, 0, 1, _THIRD), (0, _THIRD, 1, _THIRD), (0, 2 * _THIRD, 1, _THIRD)],
        "left1_right2": [(0, 0, 0.5, 1), (0.5, 0, 0.5, 0.5), (0.5, 0.5, 0.5, 0.5)],
        "top1_bottom2": [(0, 0, 1, 0.5), (0, 0.5, 0.5, 0.5), (0.5, 0.5, 0.5, 0.5)],
    },
    4: {
        "grid2x2": [
            (0, 0, 0.5, 0.5),
            (0.5, 0, 0.5, 0.5),
            (0, 0.5, 0.5, 0.5),
            (0.5, 0.5, 0.5, 0.5),
        ],
        "v4": [(0, 0, 1, 0.25), (0, 0.25, 1, 0.25), (0, 0.5, 1, 0.25), (0, 0.75, 1, 0.25)],
    },
    5: {
        "top2_bottom3": [
            (0, 0, 0.5, 0.5),
            (0.5, 0, 0.5, 0.5),
            (0, 0.5, _THIRD, 0.5),
            (_THIRD, 0.5, _THIRD, 0.5),
            (2 * _THIRD, 0.5, _THIRD, 0.5),
        ],
    },
    6: {
        "grid2x3": [
            (0, 0, 0.5, _THIRD),
            (0.5, 0, 0.5, _THIRD),
            (0, _THIRD, 0.5, _THIRD),
            (0.5, _THIRD, 0.5, _THIRD),
            (0, 2 * _THIRD, 0.5, _THIRD),
            (0.5, 2 * _THIRD, 0.5, _THIRD),
        ],
    },
}


def _collage_cells(count: int, layout: str) -> list[tuple[float, float, float, float]]:
    templates = _COLLAGE_TEMPLATES.get(count)
    if templates is None:
        raise StoryCollageLayoutError from ValueError(f"unsupported collage image count: {count}")
    cells = templates.get(layout)
    if cells is None:
        raise StoryCollageLayoutError from ValueError(
            f"unknown collage layout {layout!r} for {count} images"
        )
    return cells


def _default_collage_layout(count: int) -> str:
    """The first template id for ``count`` — the default when none is requested."""
    templates = _COLLAGE_TEMPLATES.get(count)
    if templates is None:
        raise StoryCollageLayoutError from ValueError(f"unsupported collage image count: {count}")
    return next(iter(templates))


def _compose_story_collage(images: list[bytes], layout: str) -> bytes:
    """Stitch 2..6 photos into one 1080x1920 JPEG (q90) per the ``layout`` template.

    Each source is cover-crop-fitted into its cell rect with a config-driven gap
    between cells, over a blurred background built from the first image. Raises
    ``StoryCollageLayoutError`` when the count is unsupported or the layout id is
    unknown for that count, and ``StoryImageNormalisationError`` on any
    undecodable input.
    """
    target_width, target_height = 1080, 1920
    cells = _collage_cells(len(images), layout)
    sources = [_decode_story_source(image) for image in images]
    canvas = _blurred_story_background(sources[0], target_width, target_height)
    gap = settings.profile_media.story_collage_gap_px
    for source, (x, y, w, h) in zip(sources, cells, strict=True):
        px = round(x * target_width) + gap // 2
        py = round(y * target_height) + gap // 2
        pw = round(w * target_width) - gap
        ph = round(h * target_height) - gap
        if pw > 0 and ph > 0:
            canvas.paste(_cover_crop(source, pw, ph), (px, py))
    output = BytesIO()
    canvas.save(output, format="JPEG", quality=90)
    return output.getvalue()
