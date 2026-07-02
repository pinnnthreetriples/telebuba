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

Failures raise :class:`StoryVideoNormalisationError` carrying a stable,
locale-neutral ``code`` (never pre-translated prose, non-negotiable #12);
the code flows through ``execute``'s ``ActionResult.error_message`` to the
API error envelope, and the SPA translates it.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Final, Literal

from core.config import settings

# Telegram story spec — see core.telegram.org/api/stories.
_TARGET_WIDTH: Final[int] = 720
_TARGET_HEIGHT: Final[int] = 1280
_MAX_DURATION_SEC: Final[int] = 60

# Stable, locale-neutral failure codes (non-negotiable #12). The SPA translates
# these — no Russian prose crosses the wire. One code per distinct failure.
StoryVideoErrorCode = Literal[
    "story_video_invalid",
    "story_video_thumb_failed",
    "story_video_corrupt",
    "story_video_duration_failed",
    "story_video_ffmpeg_missing",
]

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
    """Raised when ffmpeg can't produce a sendable story MP4.

    Carries a stable, locale-neutral ``code`` — ``str(exc)`` is the code itself,
    so it survives the ``execute`` → ``ActionResult.error_message`` → API
    error-envelope path as a code (the SPA translates it), never Russian prose.
    """

    def __init__(self, code: StoryVideoErrorCode) -> None:
        self.code: StoryVideoErrorCode = code
        super().__init__(code)


async def normalize_story_video_for_telegram(
    content: bytes,
) -> tuple[bytes, bytes, float, int, int]:
    """Transform arbitrary video bytes into a sendable Telegram story MP4.

    Returns ``(video_bytes, thumb_bytes, duration_sec, width, height)``.
    ``width`` and ``height`` always equal 720 / 1280 because we control
    the encode — the caller passes them straight into the
    ``DocumentAttributeVideo`` constructor without trusting the source.

    The thumbnail is extracted from the **source** video (not the
    re-encoded MP4) with the same 9:16 crop applied, so it stays consistent
    with what was published while avoiding the H.264 generation loss that
    made freshly-uploaded story thumbs look soft inside the UI carousel.

    Raises :class:`StoryVideoNormalisationError` (a ``ValueError``) carrying a
    stable ``code`` when ffmpeg is missing, the input is corrupt, or the encode
    fails — the UI layer catches it via the existing ``ValueError`` path and
    translates the code (non-negotiable #12).
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
            failure_code="story_video_invalid",
        )
        await _run_ffmpeg(
            ffmpeg_bin,
            _thumbnail_args(source_path, thumb_path),
            failure_code="story_video_thumb_failed",
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
    # Apply the same 9:16 crop+scale as the main encode so the thumbnail
    # matches the actually-published video, but pull the frame straight
    # from the source. That skips the H.264 CRF 23 generation loss that
    # made re-encoded thumbs look noticeably softer. ``-q:v 2`` is mjpeg's
    # near-max quality (range 2-31, lower = better).
    return [
        "-y",
        "-ss",
        "0.5",
        "-i",
        str(source),
        "-vf",
        _FFMPEG_ENCODE_FILTER,
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
        code: StoryVideoErrorCode = "story_video_ffmpeg_missing"
        raise StoryVideoNormalisationError(code) from exc
    bundled = imageio_ffmpeg.get_ffmpeg_exe()
    if not bundled:
        code = "story_video_ffmpeg_missing"
        raise StoryVideoNormalisationError(code)
    return bundled


async def _run_ffmpeg(binary: str, args: list[str], *, failure_code: StoryVideoErrorCode) -> str:
    """Execute ffmpeg and return its stderr.

    ffmpeg writes informational output to stderr even on success — duration
    parsing relies on that. A non-zero exit is translated into a
    ``StoryVideoNormalisationError`` carrying a stable code so the UI keeps a
    locale-neutral contract with the rest of the upload pipeline.
    """
    proc = await asyncio.create_subprocess_exec(
        binary,
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    stderr_bytes = await _communicate_or_kill(proc, failure_code)
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        if "Invalid data found" in stderr or "moov atom not found" in stderr:
            corrupt: StoryVideoErrorCode = "story_video_corrupt"
            raise StoryVideoNormalisationError(corrupt)
        raise StoryVideoNormalisationError(failure_code)
    return stderr


async def _communicate_or_kill(
    proc: asyncio.subprocess.Process,
    failure_code: StoryVideoErrorCode,
) -> bytes:
    """Await ``proc.communicate()`` under the configured ffmpeg timeout.

    On timeout the process is killed (and reaped) so a stalling video can't hang
    the request coroutine forever or orphan the child, then the module's normal
    failure path raises. Returns the captured stderr bytes on completion.
    """
    try:
        async with asyncio.timeout(settings.profile_media.ffmpeg_timeout_seconds):
            _, stderr_bytes = await proc.communicate()
    except TimeoutError as exc:
        proc.kill()
        with suppress(ProcessLookupError):
            await proc.wait()
        raise StoryVideoNormalisationError(failure_code) from exc
    return stderr_bytes


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
    stderr_bytes = await _communicate_or_kill(
        proc,
        "story_video_duration_failed",
    )
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    match = _DURATION_RE.search(stderr)
    if match is None:
        return 0.0
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
