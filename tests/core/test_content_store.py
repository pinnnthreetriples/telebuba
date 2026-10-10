"""The shared content-addressed store: photos must decode, videos need their header."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import pytest
from PIL import Image

from core.config import settings
from core.masstagging_media import MasstaggingMediaError, read_media, store_media

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _media_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.masstagging, "media_dir", tmp_path / "media")


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "blue").save(buffer, format="PNG")
    return buffer.getvalue()


_MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64


@pytest.mark.asyncio
@pytest.mark.parametrize(("content", "suffix"), [(_png(), ".png"), (_MP4, ".mp4"), (_MP4, ".mov")])
async def test_photos_and_videos_round_trip(content: bytes, suffix: str) -> None:
    media_id = await store_media(content, suffix)

    assert media_id.endswith(suffix)
    assert await read_media(media_id) == content
    assert await store_media(content, suffix) == media_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "suffix"),
    [(b"plain text", ".mp4"), (b"not an image", ".png"), (_png(), ".gif")],
)
async def test_what_is_not_a_photo_or_video_is_refused(content: bytes, suffix: str) -> None:
    with pytest.raises(MasstaggingMediaError, match="media_invalid"):
        await store_media(content, suffix)


@pytest.mark.asyncio
async def test_bad_names_and_tampered_files_read_as_missing(tmp_path: Path) -> None:
    media_id = await store_media(_MP4, ".mp4")
    with pytest.raises(MasstaggingMediaError, match="media_not_found"):
        await read_media("../etc/passwd")
    with pytest.raises(MasstaggingMediaError, match="media_not_found"):
        await read_media("0" * 64 + ".exe")
    (tmp_path / "media" / media_id).write_bytes(b"tampered")
    with pytest.raises(MasstaggingMediaError, match="media_not_found"):
        await read_media(media_id)
