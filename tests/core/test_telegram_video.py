"""Tests for the story-video normaliser — ffmpeg subprocess pipeline.

The encode + duration probe are slow on CI (~5–10 s for a 2-second clip),
so the integration round-trip is the only end-to-end test; the missing-binary
and corrupt-input failure paths are stubbed deterministically.
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.telegram_client._video import (
    StoryVideoNormalisationError,
    _communicate_or_kill,
    _extract_duration_seconds,
    _resolve_ffmpeg_binary,
    normalize_story_video_for_telegram,
)

if TYPE_CHECKING:
    from pathlib import Path


def _generate_test_video(ffmpeg_bin: str, output: Path, *, width: int, height: int) -> None:
    """Generate a 2-second test MP4 via the system ffmpeg's ``testsrc`` source.

    Used to drive the normaliser with a real input instead of a hand-rolled
    MP4 blob. We use the same ffmpeg binary the normaliser uses so the test
    has the same availability characteristics as production.
    """
    import subprocess  # noqa: PLC0415 — test-only sync wrapper

    subprocess.run(  # noqa: S603 — ffmpeg_bin resolves to a trusted binary, all args are constants
        [
            ffmpeg_bin,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={width}x{height}:rate=30:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            "-shortest",
            "-pix_fmt",
            "yuv420p",
            str(output),
        ],
        check=True,
        capture_output=True,
    )


@pytest.mark.asyncio
async def test_normalize_story_video_produces_720x1280_mp4_with_thumb(
    tmp_path: Path,
) -> None:
    """End-to-end: a real 640x360 16:9 source becomes a 720x1280 9:16 story.

    Asserts:

    - Output is a real MP4 (starts with the ``ftyp`` box signature).
    - Reported dimensions match the centre-cropped story canvas (720x1280).
    - Duration parses to roughly the source length, not zero.
    - Thumbnail bytes start with the JPEG SOI marker.
    """
    ffmpeg = _resolve_ffmpeg_binary()
    source_path = tmp_path / "src.mp4"
    _generate_test_video(ffmpeg, source_path, width=640, height=360)

    video, thumb, duration, width, height = await normalize_story_video_for_telegram(
        source_path.read_bytes(),
    )

    assert width == 720
    assert height == 1280
    assert 1.0 < duration < 5.0, "duration probe must read the encoded length"
    # MP4 files start with the 'ftyp' box at offset 4.
    assert video[4:8] == b"ftyp", "normaliser must output a real MP4"
    # JPEG SOI marker.
    assert thumb[:2] == b"\xff\xd8", "thumbnail must be a real JPEG"


@pytest.mark.asyncio
async def test_normalize_story_video_rejects_corrupt_input() -> None:
    """A random byte blob fails the ffmpeg decode step with a stable code.

    The error carries a locale-neutral code (never Russian prose) so it can
    cross the API wire and be translated by the SPA (non-negotiable #12).
    """
    with pytest.raises(StoryVideoNormalisationError) as exc_info:
        await normalize_story_video_for_telegram(b"not actually video content")
    # Corrupt/undecodable input → the ffmpeg-decode-failure code (or the
    # generic invalid code, depending on how ffmpeg classifies the blob).
    assert exc_info.value.code in {"story_video_corrupt", "story_video_invalid"}
    assert str(exc_info.value) == exc_info.value.code


@pytest.mark.asyncio
async def test_normalize_story_video_reports_ffmpeg_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If neither system ffmpeg nor imageio-ffmpeg resolves, raise cleanly.

    The UI catches ``ValueError`` (which ``StoryVideoNormalisationError``
    subclasses); the raised error carries a stable code the SPA translates.

    Forces ``shutil.which`` to return None and ejects ``imageio_ffmpeg`` from
    ``sys.modules`` while blocking its re-import — avoids the ``__import__``
    monkeypatch trick that recurses through Python's import machinery for
    every dependent module load.
    """
    import sys  # noqa: PLC0415 — test-only sys.modules manipulation

    monkeypatch.setattr("core.telegram_client._video.shutil.which", lambda _name: None)
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)

    with pytest.raises(StoryVideoNormalisationError) as exc_info:
        await normalize_story_video_for_telegram(b"any-bytes")
    assert exc_info.value.code == "story_video_ffmpeg_missing"


@pytest.mark.asyncio
async def test_extract_duration_seconds_parses_ffmpeg_stderr(tmp_path: Path) -> None:
    """ffprobe-less duration probe must parse the ``Duration: HH:MM:SS.SS`` line.

    Regression guard: we deliberately skip ffprobe (imageio-ffmpeg only
    bundles ffmpeg, not ffprobe), so the parser must keep up if a future
    ffmpeg version reshuffles its stderr template.
    """
    ffmpeg = _resolve_ffmpeg_binary()
    sample = tmp_path / "sample.mp4"
    _generate_test_video(ffmpeg, sample, width=320, height=240)

    duration = await _extract_duration_seconds(ffmpeg, sample)

    assert 1.5 < duration < 3.0, "ffmpeg's own stderr Duration: line must parse"


class _HangingProc:
    """Fake subprocess whose ``communicate()`` never returns until killed."""

    returncode: int | None = None

    def __init__(self) -> None:
        self.killed = False
        self.waited = False

    async def communicate(self) -> tuple[bytes, bytes]:
        # Block far longer than the (tiny) test timeout so wait_for fires first.
        await asyncio.sleep(3600)
        return (b"", b"")  # pragma: no cover - never reached

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        self.waited = True
        return -9


@pytest.mark.asyncio
async def test_communicate_or_kill_times_out_and_kills(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stalling ffmpeg must be killed and surfaced with the passed failure code."""
    monkeypatch.setattr(settings.profile_media, "ffmpeg_timeout_seconds", 0.05)
    proc = _HangingProc()

    with pytest.raises(StoryVideoNormalisationError) as exc_info:
        await _communicate_or_kill(proc, "story_video_invalid")  # ty: ignore[invalid-argument-type]

    assert exc_info.value.code == "story_video_invalid"
    assert proc.killed is True, "a timed-out process must be killed"
    assert proc.waited is True, "the killed process must be reaped"


@pytest.mark.asyncio
async def test_normalize_story_video_times_out_on_stalling_ffmpeg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: a hanging ffmpeg subprocess makes the normaliser fail, not hang."""
    monkeypatch.setattr("core.telegram_client._video.shutil.which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(settings.profile_media, "ffmpeg_timeout_seconds", 0.05)

    proc = _HangingProc()

    async def fake_create_subprocess_exec(*_args: object, **_kwargs: object) -> _HangingProc:
        return proc

    monkeypatch.setattr(
        "core.telegram_client._video.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    with pytest.raises(StoryVideoNormalisationError):
        await normalize_story_video_for_telegram(b"any-bytes")

    assert proc.killed is True


def test_duration_regex_matches_expected_ffmpeg_format() -> None:
    """Tight unit guard for the regex without invoking ffmpeg."""
    from core.telegram_client._video import _DURATION_RE  # noqa: PLC0415 — internal regex

    line = "  Duration: 00:01:23.45, start: 0.000000, bitrate: 1234 kb/s"
    match = _DURATION_RE.search(line)
    assert match is not None
    hours, minutes, seconds = match.groups()
    total = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    assert total == pytest.approx(83.45)
    # Sanity-check the pattern isn't a one-off — confirm with a fresh
    # compiled regex too.
    assert re.search(r"Duration:\s*\d+:\d+:\d+", line)
