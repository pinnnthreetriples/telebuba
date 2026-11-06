"""Link-sticker overlay in the story-video normaliser: the ffmpeg arguments and a real run."""

from __future__ import annotations

import subprocess
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from core.telegram_client._video import _resolve_ffmpeg_binary, normalize_story_video_for_telegram
from core.telegram_client._video_args import STORY_VIDEO_SIZE, _encode_args, _thumbnail_args

_SOURCE = Path("in.bin")
_OUT = Path("out.mp4")
_OVERLAY = Path("overlay.png")


def test_without_an_overlay_the_encode_is_the_plain_filter() -> None:
    args = _encode_args(_SOURCE, _OUT)

    assert "-vf" in args
    assert "-filter_complex" not in args
    assert args.count("-i") == 1


def test_an_overlay_becomes_a_second_input_and_a_filter_graph() -> None:
    args = _encode_args(_SOURCE, _OUT, _OVERLAY)

    inputs = [args[i + 1] for i, value in enumerate(args) if value == "-i"]
    assert inputs == [str(_SOURCE), str(_OVERLAY)]
    assert "-vf" not in args
    graph = args[args.index("-filter_complex") + 1]
    # Crop and scale first, so the overlay lands on the finished 720x1280 canvas.
    assert graph.index("crop=") < graph.index("scale=720:1280") < graph.index("overlay=0:0")
    assert graph.endswith("format=yuv420p[out]")
    # Explicit stream selection: the picture from the graph, the audio when there is any.
    maps = [args[i + 1] for i, value in enumerate(args) if value == "-map"]
    assert maps == ["[out]", "0:a?"]
    # Both inputs come before the output options, so ``-t`` still caps the OUTPUT.
    assert args.index("-t") > args.index(str(_OVERLAY))
    assert args[-1] == str(_OUT)


def test_the_thumbnail_gets_the_overlay_too_and_keeps_its_seek() -> None:
    args = _thumbnail_args(_SOURCE, Path("t.jpg"), _OVERLAY)

    assert args[args.index("-ss") + 1] == "0.5"
    assert args.index("-ss") < args.index(str(_SOURCE)) < args.index(str(_OVERLAY))
    assert "-filter_complex" in args
    assert [args[i + 1] for i, v in enumerate(args) if v == "-map"] == ["[out]"]
    assert "-vf" in _thumbnail_args(_SOURCE, Path("t.jpg"))


def _make_source(ffmpeg: str, output: Path) -> None:
    subprocess.run(  # noqa: S603 - trusted binary, constant arguments
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:size=640x360:rate=30:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(output),
        ],
        check=True,
        capture_output=True,
    )


@pytest.mark.asyncio
async def test_the_overlay_is_burnt_into_a_real_video_and_its_thumbnail(tmp_path: Path) -> None:
    """The source has no audio track, which also proves ``-map 0:a?`` is optional."""
    ffmpeg = _resolve_ffmpeg_binary()
    source = tmp_path / "src.mp4"
    _make_source(ffmpeg, source)
    overlay = Image.new("RGBA", STORY_VIDEO_SIZE, (0, 0, 0, 0))
    overlay.paste((255, 0, 0, 255), (100, 100, 300, 300))
    png = BytesIO()
    overlay.save(png, format="PNG")

    video, thumb, _duration, width, height = await normalize_story_video_for_telegram(
        source.read_bytes(), overlay_png=png.getvalue()
    )

    assert (width, height) == STORY_VIDEO_SIZE
    assert video[4:8] == b"ftyp"
    with Image.open(BytesIO(thumb)) as frame:
        assert frame.size == STORY_VIDEO_SIZE
        red = frame.convert("RGB").getpixel((200, 200))
        elsewhere = frame.convert("RGB").getpixel((600, 1000))
    assert isinstance(red, tuple)
    assert red[0] > 180
    assert red[1] < 90
    assert isinstance(elsewhere, tuple)
    assert elsewhere[2] > elsewhere[0]  # the blue source, not the overlay
