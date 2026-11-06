"""ffmpeg argument lists for the story-video normaliser (see ``_video``).

Split out of ``_video`` for the file-size budget; nothing here touches a process.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from pathlib import Path

# Telegram story spec — see core.telegram.org/api/stories.
TARGET_WIDTH: Final[int] = 720
TARGET_HEIGHT: Final[int] = 1280
MAX_DURATION_SEC: Final[int] = 60
STORY_VIDEO_SIZE: Final[tuple[int, int]] = (TARGET_WIDTH, TARGET_HEIGHT)

_FFMPEG_ENCODE_FILTER: Final[str] = (
    # Crop the largest 9:16 rectangle that fits inside the source, then scale
    # to the canvas. Mirrors the official Android editor's behaviour.
    "crop='min(iw,ih*9/16)':'min(ih,iw*16/9)',"
    f"scale={TARGET_WIDTH}:{TARGET_HEIGHT}:flags=lanczos,format=yuv420p"
)

# With link stickers a second input (a transparent PNG at the canvas size) is laid over
# the cropped, scaled picture, so the plates are burnt in exactly where the tap areas are.
_OVERLAY_FILTER: Final[str] = (
    f"[0:v]{_FFMPEG_ENCODE_FILTER.removesuffix(',format=yuv420p')}[base];"
    "[base][1:v]overlay=0:0:format=auto,format=yuv420p[out]"
)


def _filter_args(overlay: Path | None, *, audio: bool) -> list[str]:
    """The picture filter: a plain ``-vf``, or a graph that also lays the stickers over.

    A filter graph switches ffmpeg to explicit stream selection, so the audio (when the
    output keeps any) is mapped by hand, and optionally: a silent source has none.
    """
    if overlay is None:
        return ["-vf", _FFMPEG_ENCODE_FILTER]
    args = ["-filter_complex", _OVERLAY_FILTER, "-map", "[out]"]
    return [*args, "-map", "0:a?"] if audio else args


def _inputs(source: Path, overlay: Path | None, *, seek: str | None = None) -> list[str]:
    args = ["-y"]
    if seek is not None:
        args += ["-ss", seek]
    args += ["-i", str(source)]
    if overlay is not None:
        # A single still: the overlay filter holds its last frame for the whole video.
        args += ["-i", str(overlay)]
    return args


def _encode_args(source: Path, output: Path, overlay: Path | None = None) -> list[str]:
    return [
        *_inputs(source, overlay),
        "-t",
        str(MAX_DURATION_SEC),
        *_filter_args(overlay, audio=True),
        "-c:v",
        "libx264",
        "-profile:v",
        "main",
        "-level",
        "4.0",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-r",
        "30",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ac",
        "2",
        "-ar",
        "44100",
        "-movflags",
        "+faststart",
        "-pix_fmt",
        "yuv420p",
        str(output),
    ]


def _thumbnail_args(source: Path, thumb: Path, overlay: Path | None = None) -> list[str]:
    # Apply the same 9:16 crop+scale as the main encode so the thumbnail
    # matches the actually-published video, but pull the frame straight
    # from the source. That skips the H.264 CRF 23 generation loss that
    # made re-encoded thumbs look noticeably softer. ``-q:v 2`` is mjpeg's
    # near-max quality (range 2-31, lower = better). With stickers the same
    # overlay graph runs, so the poster shows the plates too.
    return [
        *_inputs(source, overlay, seek="0.5"),
        *_filter_args(overlay, audio=False),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(thumb),
    ]
