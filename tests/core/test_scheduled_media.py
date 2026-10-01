"""The content-addressed scheduled-media store (``core.scheduled_media``)."""

from __future__ import annotations

import hashlib
import io
import os
import time
from typing import TYPE_CHECKING

import pytest
from PIL import Image

from core import scheduled_media
from core.config import settings

if TYPE_CHECKING:
    from pathlib import Path


def _png(color: str = "red") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _staged(tmp_path: Path, content: bytes, name: str = "upload.png") -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


def _age(path: Path, seconds: int) -> None:
    old = time.time() - seconds
    os.utime(path, (old, old))


@pytest.mark.asyncio
async def test_ingest_names_the_file_by_its_content_and_stores_it_once(tmp_path: Path) -> None:
    content = _png()
    first = await scheduled_media.ingest(_staged(tmp_path, content, "a.png"), ".png")
    second = await scheduled_media.ingest(_staged(tmp_path, content, "b.png"), ".png")

    assert (first.name, first.size) == (second.name, second.size)
    assert (first.created, second.created) == (True, False)
    assert first.name == f"{hashlib.sha256(content).hexdigest()}.png"
    assert first.size == len(content)
    stored = list(settings.scheduled_posts.media_dir.iterdir())
    assert [path.name for path in stored] == [first.name]


@pytest.mark.asyncio
async def test_read_media_returns_the_bytes_and_refuses_a_corrupted_file(tmp_path: Path) -> None:
    stored = await scheduled_media.ingest(_staged(tmp_path, _png()), ".png")
    assert await scheduled_media.read_media(stored.name) == _png()

    (settings.scheduled_posts.media_dir / stored.name).write_bytes(b"truncated")
    with pytest.raises(scheduled_media.MediaStoreError) as caught:
        await scheduled_media.read_media(stored.name)
    assert caught.value.code == "scheduled_media_missing"


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["../../telebuba.db", "a" * 64 + ".exe", "x.png", ""])
async def test_only_a_well_formed_name_ever_becomes_a_path(name: str) -> None:
    with pytest.raises(scheduled_media.MediaStoreError):
        await scheduled_media.read_media(name)
    assert await scheduled_media.touch_existing(name) is False


@pytest.mark.asyncio
async def test_verify_image_refuses_bytes_pillow_cannot_decode(tmp_path: Path) -> None:
    await scheduled_media.verify_image(_staged(tmp_path, _png()))
    with pytest.raises(scheduled_media.MediaStoreError) as caught:
        await scheduled_media.verify_image(_staged(tmp_path, b"not an image", "fake.png"))
    assert caught.value.code == "scheduled_media_invalid"


@pytest.mark.asyncio
async def test_gc_spares_referenced_and_young_files_and_drops_the_rest(tmp_path: Path) -> None:
    kept = await scheduled_media.ingest(_staged(tmp_path, _png("red"), "r.png"), ".png")
    young = await scheduled_media.ingest(_staged(tmp_path, _png("blue"), "b.png"), ".png")
    old = await scheduled_media.ingest(_staged(tmp_path, _png("green"), "g.png"), ".png")
    directory = settings.scheduled_posts.media_dir
    _age(directory / kept.name, 7200)
    _age(directory / old.name, 7200)
    stray = directory / "notes.txt"
    stray.write_text("operator file")
    _age(stray, 7200)

    removed = await scheduled_media.collect_garbage(frozenset({kept.name}), grace_seconds=3600)

    assert removed == 1
    assert {path.name for path in directory.iterdir()} == {kept.name, young.name, "notes.txt"}


@pytest.mark.asyncio
async def test_reingesting_an_old_file_restarts_its_grace(tmp_path: Path) -> None:
    content = _png()
    stored = await scheduled_media.ingest(_staged(tmp_path, content), ".png")
    _age(settings.scheduled_posts.media_dir / stored.name, 7200)

    await scheduled_media.ingest(_staged(tmp_path, content, "again.png"), ".png")

    assert await scheduled_media.collect_garbage(frozenset(), grace_seconds=3600) == 0


@pytest.mark.asyncio
async def test_gc_drops_abandoned_temporary_files(tmp_path: Path) -> None:
    await scheduled_media.ingest(_staged(tmp_path, _png()), ".png")
    temporary = settings.scheduled_posts.media_dir / ".tmp_abandoned.png"
    temporary.write_bytes(b"partial")
    _age(temporary, 7200)

    await scheduled_media.collect_garbage(frozenset(), grace_seconds=3600)

    assert not temporary.exists()


@pytest.mark.asyncio
async def test_thumbnail_is_a_small_jpeg_and_none_for_video_or_missing(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (2000, 1000), "white").save(buffer, format="PNG")
    stored = await scheduled_media.ingest(_staged(tmp_path, buffer.getvalue()), ".png")

    thumb = await scheduled_media.thumbnail(stored.name)

    assert thumb is not None
    with Image.open(io.BytesIO(thumb)) as image:
        assert image.format == "JPEG"
        assert max(image.size) <= settings.scheduled_posts.thumb_max_px
    assert await scheduled_media.thumbnail("a" * 64 + ".mp4") is None
    assert await scheduled_media.thumbnail("b" * 64 + ".png") is None
