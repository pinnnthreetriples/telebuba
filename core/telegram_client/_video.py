"""Story-video normalisation via ffmpeg subprocess.

Telegram rejects story videos that aren't ~9:16 H.264/AAC MP4 with
``+faststart`` and ``supports_streaming=True``. We never trust the
operator's source file directly — every video gets re-encoded through
ffmpeg before it hits ``upload_file``. The official Android client does
the same thing locally before sending (StoryEntry / VideoEditedInfo
pipeline through MediaCodec).

Tool choice (June 2026):

- ``ffmpeg-python`` (kkroening) is effectively unmaintained — last commit
  July 2022, last release 2019 — so we avoid it.
- Pure ``asyncio.create_subprocess_exec`` against the ffmpeg binary is
  the production-recommended approach in 2026.
- Resolution falls back to ``imageio-ffmpeg``'s bundled binary if no
  system ffmpeg is on PATH, so deployments don't need a side-channel
  install step.

The cropping strategy matches official Telegram clients (center-crop
to 9:16, no blurred letterbox — that's a photo-only style). Output is
720x1280 H.264 main / AAC stereo @ 30 fps, time-capped to 60 s.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import tempfile
from pathlib import Path
from typing import Final

# Telegram story spec — see core.telegram.org/api/stories.
_TARGET_WIDTH: Final[int] = 720
_TARGET_HEIGHT: Final[int] = 1280
_MAX_DURATION_SEC: Final[int] = 60

_FFMPEG_ENCODE_FILTER: Final[str] = (
    # Crop the largest 9:16 rectangle that fits inside the source, then scale
    # to the canvas. Mirrors the official Android editor's behaviour.
    "crop='min(iw,ih*9/16)':'min(ih,iw*16/9)',"
    f"scale={_TARGET_WIDTH}:{_TARGET_HEIGHT}:flags=lanczos,format=yuv420p"
)

# Duration line ffmpeg writes to stderr — e.g. ``Duration: 00:00:08.04,``.
# ffmpeg is the only binary we strictly require; ffprobe ships separately on
# many distros (and not at all with imageio-ffmpeg), so we parse the encoder's
# own stderr instead of spawning ffprobe.
_DURATION_RE: Final[re.Pattern[str]] = re.compile(
    r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",
)


class StoryVideoNormalisationError(ValueError):
    """Raised when ffmpeg can't produce a sendable story MP4."""


async def normalize_story_video_for_telegram(
    content: bytes,
) -> tuple[bytes, bytes, float, int, int]:
    """Transform arbitrary video bytes into a sendable Telegram story MP4.

    Returns ``(video_bytes, thumb_bytes, duration_sec, width, height)``.
    ``width`` and ``height`` always equal 720 / 1280 because we control
    the encode — the caller passes them straight into the
    ``DocumentAttributeVideo`` constructor without trusting the source.

    Raises :class:`StoryVideoNormalisationError` (a ``ValueError``) with a
    Russian-language message when ffmpeg is missing, the input is corrupt,
    or the encode fails — the UI layer catches it via the existing
    ``ValueError`` path and surfaces the message verbatim.
    """
    ffmpeg_bin = _resolve_ffmpeg_binary()
    with tempfile.TemporaryDirectory() as tempdir:
        td = Path(tempdir)
        source_path = td / "input.bin"
        output_path = td / "story.mp4"
        thumb_path = td / "thumb.jpg"
        source_path.write_bytes(content)
        await _run_ffmpeg(
            ffmpeg_bin,
            _encode_args(source_path, output_path),
            failure_message="Видео не удалось обработать — попробуйте другой файл",
        )
        await _run_ffmpeg(
            ffmpeg_bin,
            _thumbnail_args(output_path, thumb_path),
            failure_message="Превью видео извлечь не удалось",
        )
        duration = await _extract_duration_seconds(ffmpeg_bin, output_path)
        return (
            output_path.read_bytes(),
            thumb_path.read_bytes(),
            duration,
            _TARGET_WIDTH,
            _TARGET_HEIGHT,
        )


def _encode_args(source: Path, output: Path) -> list[str]:
    return [
        "-y",
        "-i",
        str(source),
        "-t",
        str(_MAX_DURATION_SEC),
        "-vf",
        _FFMPEG_ENCODE_FILTER,
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


def _thumbnail_args(source: Path, thumb: Path) -> list[str]:
    # ``-q:v 2`` is mjpeg's near-max quality (range 2-31, lower = better) — the
    # default ``3`` was visibly noisy when rendered inside the story carousel.
    return [
        "-y",
        "-ss",
        "0.5",
        "-i",
        str(source),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(thumb),
    ]


def _resolve_ffmpeg_binary() -> str:
    """Find ffmpeg — system PATH first, then imageio-ffmpeg's bundled binary.

    System ffmpeg is preferred because it's almost always newer than what
    imageio-ffmpeg pins. The fallback exists so deployments can ship without
    a separate apt/brew step.
    """
    system = shutil.which("ffmpeg")
    if system is not None:
        return system
    try:
        import imageio_ffmpeg  # noqa: PLC0415 — optional fallback path
    except ImportError as exc:
        msg = "ffmpeg не установлен в системе — установите ffmpeg или зависимость imageio-ffmpeg"
        raise StoryVideoNormalisationError(msg) from exc
    bundled = imageio_ffmpeg.get_ffmpeg_exe()
    if not bundled:
        msg = "ffmpeg не установлен в системе"
        raise StoryVideoNormalisationError(msg)
    return bundled


async def _run_ffmpeg(binary: str, args: list[str], *, failure_message: str) -> str:
    """Execute ffmpeg and return its stderr.

    ffmpeg writes informational output to stderr even on success — duration
    parsing relies on that. A non-zero exit is translated into a Russian
    ``StoryVideoNormalisationError`` so the UI message stays consistent
    with the rest of the upload pipeline.
    """
    proc = await asyncio.create_subprocess_exec(
        binary,
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr_bytes = await proc.communicate()
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        if "Invalid data found" in stderr or "moov atom not found" in stderr:
            msg = "Видео повреждено или формат не поддерживается"
            raise StoryVideoNormalisationError(msg)
        raise StoryVideoNormalisationError(failure_message)
    return stderr


async def _extract_duration_seconds(binary: str, path: Path) -> float:
    """Parse the ``Duration:`` line ffmpeg prints when given ``-i`` only.

    ``ffmpeg -i <path>`` exits non-zero because no output is requested, but
    it always emits the input's metadata on stderr first. That's enough to
    recover the encoded duration without a separate ffprobe binary.
    """
    proc = await asyncio.create_subprocess_exec(
        binary,
        "-i",
        str(path),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr_bytes = await proc.communicate()
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    match = _DURATION_RE.search(stderr)
    if match is None:
        return 0.0
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
